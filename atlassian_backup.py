import requests
import json
import time
import datetime
from sys import stdout, exit
import argparse, getpass
import os
import logging
from logging.handlers import SysLogHandler

#Argparse action extension to alternatively accept passwords directly
#or via a prompt in the console
class Password(argparse.Action):
    def __call__(self, parser, namespace, values, option_string):
        if values is None:
            values = getpass.getpass()
        setattr(namespace, self.dest, values)

def set_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance', '-i', help='Atlassian account instance (format: account.attlassian.net)',\
    required=True)
    parser.add_argument('--application', '-a', help='Atlassian application to backup (Jira or Confluence)',\
    required=True)
    parser.add_argument('--username', '-u', help='Atlassian username used to login to the web application',\
    required=True)
    parser.add_argument('--timeout', '-t', help='Timeout for the remote backup to complete, defaults to 180 minutes', default=180)
    parser.add_argument('--password', '-p', action=Password, nargs='?', dest='password', \
    help='Atlassian password used to login into the web application', required=True)
    parser.add_argument('--location', '-l', default='/tmp/', help='Location of resulting backup file,\
                    defaults to \'/tmp/APPLICATION\'')
    parser.add_argument('--tasks', nargs='+', help='Perform specified tasks\
                        (with a space between each task): t (trigger), m (monitor) and d (download).\
                        If d add argument for file name')
    parser.add_argument('--log', help='Enable logging', dest='log', action='store_true')
    args = parser.parse_args()

    #Ensure valid tasks where supplied
    if args.tasks is not None:
        targs = ['t', 'm', 'd']
        if all((x not in args.tasks for x in targs)):
            print "Error: please supply either 't' (trigger), 'm' (monitor) or d (download) with tasks"
            exit(1)
    return args

class LogMessage:
    '''
    Simple local syslog logger
    '''
    def __init__(self, application):
        self._logger = logging.getLogger('BackupLogger')
        self._logger.setLevel(logging.INFO)
        self._handler = logging.handlers.SysLogHandler(address='/dev/log')
        self._logger.addHandler(self._handler)
        self._tag = str.upper('ATLASSIAN_BACKUP_' + application)
        self._format = logging.Formatter('%(name)s %(tag)s: %(message)s')
        self._handler.setFormatter(self._format)
        self._logger.addHandler

    def log_message(self, message):
        global log
        if log:
            self._logger.info(message, extra={'tag': self._tag})

def set_urls():
    global trigger_url
    global progress_url
    global download_url
    if application.upper() == 'CONFLUENCE':
        trigger_url = 'https://' + instance + '/wiki/rest/obm/1.0/runbackup'
        progress_url = 'https://' + instance + '/wiki/rest/obm/1.0/getprogress.json'
        download_url = 'https://' + instance + '/wiki/download/'
        return
    if application.upper() == 'JIRA':
        trigger_url = 'https://' + instance + '/rest/obm/1.0/runbackup'
        progress_url = 'https://' + instance + '/rest/obm/1.0/getprogress.json'
        download_url = 'https://' + instance
        return
    if application.upper() != 'JIRA' or 'CONFLUENCE':
        print "Invalid application specified. Request either \"Jira\" or \"Confluence\""
        exit(1)

def create_session(username, password):
    s = requests.session()
    #create a session
    r = s.post('https://' + instance + '/login', {'username': username, 'password': password})
    if int(r.status_code) == 200:
        return s
    else:
        print "Session creation failed"
        exit(1)

def trigger(s):
    r = s.post(url=trigger_url, data=json.dumps({'cbAttachments': 'true'}), \
        headers={'Content-Type': 'application/json', 'X-Atlassian-Token': 'no-check', 'X-Requested-With': 'XMLHttpRequest'})
    print "Trigger response: %s" % r.status_code
    if int(r.status_code) == 200:
        print "Trigger response successful"
        result = ['Trigger response successful', True]
        return result
    else:
        print 'Trigger failed'
        if int(r.status_code) == 500:
            print 'Returned text data: %s' % str(r.text)
        result = ['Trigger failed with message: %s' % str(r.text), False]
        return result

def monitor(s):
    r = s.get(url=progress_url)
    try:
        progress_data = json.loads(r.text)
    except ValueError:
        print """No JSON object could be decoded - get progress failed to return expected data.
        Return code: %s """ % (r.status_code)
        result = ['No JSON object could be decoded - get progress failed to return expected data\
        Return code: %s """ % (r.status_code)', False]
    #Timeout waiting for remote backup to complete (since it sometimes fails) in 5s multiples
    global timeout
    timeout_count = timeout*12 #timeout x 12 = number of iterations of 5s
    time_left = timeout
    while 'fileName' not in progress_data or timeout_count > 0:
        stdout.write("\r\x1b[2k") #Clears the line before re-writing to avoid artifacts
        stdout.write("\r\x1b[2K%s. Timeout remaining: %sm" \
            % (progress_data['alternativePercentage'], str(time_left)))
        stdout.flush()
        r = s.get(url=progress_url)
        progress_data = json.loads(r.text)
        time.sleep(5)
        timeout_count = timeout_count - 5
        if timeout_count%12 == 0:
            time_left = time_left - 1
    if 'fileName' in progress_data:
        result = [progress_data['fileName'], True]
        return result

def get_filename(s):
    print "Fetching file name"
    r = s.get(url=progress_url)
    try:
        progress_data = json.loads(r.text)
    except ValueError:
        print """No JSON object could be decoded - get progress failed to return expected data.
        Return code: %s """ % (r.status_code)
        return False
    if 'fileName' not in progress_data:
        print 'File name to download not found in server response.'
        return False
    else:
        return progress_data['fileName']

def create_backup_location(l):
    if not os.path.exists(location):
        try:
            os.makedirs(location, 0744)
        except OSError:
            return False
    return True

def download(s, l):
    filename = get_filename(s)
    if not filename:
        return False
    print "Filename found: %s" % filename
    print "Checking if url is valid"
    r = s.get(url=download_url + filename, stream=True)
    print "Status code: %s" % str(r.status_code)
    if int(r.status_code) == 200:
        print "Url returned '200', downloading file"
        if not create_backup_location(l):
            result = ['Failed to create backup location', False]
            return result
        date_time = datetime.datetime.now().strftime("%Y%m%d")
        with open(l + '/' + application + '-' + date_time + '.zip', 'wb') as f:
            file_total = 0
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
                    file_total = file_total + 1024
                    file_total_m = float(file_total)/1048576
                    stdout.write("\r\x1b[2k") #Clears the line before re-writing to avoid artifacts
                    stdout.write("\r\x1b[2K%.2fMB   downloaded" % file_total_m)
                    stdout.flush()
        stdout.write("\n")
        result = ['Backup downloaded successfully', True]
        return result
    else:
        print "Download file not found on remote server - response code %s" % str(r.status_code)
        print "Download url: %s" % download_url + filename
        result = ['Download file not found on remote server', False]
        return result

if __name__ == "__main__":
    args = set_arguments()
    global  trigger_url, progress_url, download_url,\
        instance, application, log, timeout
    application = args.application
    instance = args.instance + ".atlassian.net"
    username = args.username
    password = args.password
    location = args.location
    timeout = args.timeout

    if args.log:
        log = True

    set_urls()

    log = LogMessage(application)

    session = create_session(username, password)

    if args.tasks is None or 't' in args.tasks:
        print "Triggering backup"
        result = trigger(session)
        log.log_message(result[0])
        if not result[1]:
            exit(1)

    if args.tasks is None or 'm' in args.tasks:
        print "Monitoring remote backup progress"
        result = monitor(session)
        log.log_message(result[0])
        if not result[1]:
            exit(1)

    if args.tasks is None or 'd' in args.tasks:
        print "Downloading file"
        result = download(session, location)
        log.log_message(result[0])
        if not result[1]:
            exit(1)

    log.log_message('All tasks completed successfully')
    print "All tasks completed."
