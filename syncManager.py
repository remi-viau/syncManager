#!/usr/bin/python3
#    _____                  __  __
#   / ____|                |  \/  |
#  | (___  _   _ _ __   ___| \  / | __ _ _ __   __ _  __ _  ___ _ __
#   \___ \| | | | '_ \ / __| |\/| |/ _` | '_ \ / _` |/ _` |/ _ \ '__|
#   ____) | |_| | | | | (__| |  | | (_| | | | | (_| | (_| |  __/ |
#  |_____/ \__, |_| |_|\___|_|  |_|\__,_|_| |_|\__,_|\__, |\___|_|
#           __/ |                                     __/ |
#          |___/                                     |___/
# Version 3.1 - 21 - 11 - 2024
# Writed by : Rémi Viau
# Script intented to backup/restore static data folder and service database to/from this own S3 bucket on OVH S3
#
# Run this script with docker exec -it -u root
#
from datetime import datetime
from pathlib import Path
import os, subprocess, argparse, configparser, sys, time

##argument
parser = argparse.ArgumentParser(description='This program is intented to backup and restore  all wordpress content and data to/from S3')
parser.add_argument('--backup', action='store_true',help='Create a backup and upload it on S3')
parser.add_argument('--restore', action='store_true', help='Restore the latest backup from S3')
parser.add_argument('--show', action='store_true', help='Show available restore point from choosedn envirronement')
parser.add_argument('--env', help='The name of the env to backup/restore on S3 (dev or prod)', required=True)
parser.add_argument('--date', help='The date to restore based on folder name on S3 (default to latest)', default="latest")
parser.add_argument('--extra', help='Path to extra script to start after restore')
args = parser.parse_args()

#load configuration file
config = configparser.ConfigParser()
config.read(os.path.dirname(os.path.realpath(__file__))+'/syncManager.ini')

# Function to load env var or failover on the configuration file
def get_value(env_var, config_section, config_key, as_list=False):
    value = os.getenv(env_var) or config.get(config_section, config_key)
    return value.split(',') if as_list else value

##Script env configuration
scriptsDir = os.path.dirname(os.path.realpath(__file__))+"/"
servicename = config.get("info", "servicename")
pathsList = get_value('PATH_LIST', 'pathListTobackup', 'path', as_list=True)
dbList = get_value('DATABASE_NAME', 'dbListTobackup', 'db', as_list=True)
# Récupérer les données
dbadmin = get_value('DATABASE_USERNAME', 'databaseCredentials', 'dbadmin')
dbpassword = get_value('DATABASE_PASSWORD', 'databaseCredentials', 'dbpassword')
dbhost = get_value('DATABASE_HOST', 'databaseCredentials', 'dbhost')
s3AccessKey = get_value('S3_BACKUP_ACCESS_KEY', 's3Credentials', 's3AccessKey')
s3SecretKey = get_value('S3_BACKUP_SECRET_KEY', 's3Credentials', 's3SecretKey')
s3AccessKeyDev = get_value('S3_BACKUP_ACCESS_KEY_DEV', 's3Credentials', 's3AccessKeyDev')
s3SecretKeyDev = get_value('S3_BACKUP_SECRET_KEY_DEV', 's3Credentials', 's3SecretKeyDev')
retentionDays = config.getint("backupSettings", "retentionDays", fallback=30)

#internal var generation
if args.env == "dev":
    s3AccessKey = s3AccessKeyDev
    s3SecretKey = s3SecretKeyDev
    regionS3 = {
        "primary-dev": config.get("regionS3", "primary"),
        "secondary-dev": config.get("regionS3", "secondary")
    }
else:
    regionS3 = {
        "primary": config.get("regionS3", "primary"),
        "secondary": config.get("regionS3", "secondary")
    }
    
workingDir = scriptsDir+'temp/'
currentDate = datetime.now()
pathDate = currentDate.strftime('%Y%m%d-%H%M%S')
dumpDirPath = workingDir + pathDate
excludedDbs = ['mysql','information_schema','performance_schema', 'sys']    
start_time = time.time()

class bcolors:
    DEFAULT='\033[97m'
    OKGREEN = "\033[92m"
    HEADER = "\033[95m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"

def progress(text):
    if text == "done":
        return print(f"{bcolors.OKGREEN} ✔{bcolors.DEFAULT}")
    elif text[0] == "!":
        return print(f"{bcolors.WARNING}{text}{bcolors.DEFAULT}")
    elif text[0] == "+":
        return print(f"{bcolors.FAIL}{text}{bcolors.DEFAULT}")
    elif text[0] == "-":
        return print(f"{text}", end="", flush=True)
    else:
        return print(f"{bcolors.HEADER}{text}{bcolors.DEFAULT}")

#testing mandatory var presence
def testVars():
    mandatory_vars = [
        ("-- Service name", servicename),
        ("-- S3 Access key", s3AccessKey),
        ("-- S3 Secret key", s3SecretKey),
    ]
    errors = []
    # Check vars
    for var_name, var_value in mandatory_vars:
        if not var_value:
            errors.append(f"{var_name} not found")

    # if errors print them then exit
    if errors:
        progress("+! Missing mandatory params")
        progress("\n".join(errors))
        progress("Stopping process")
        sys.exit(1)
testVars()

#function to check base foder if needed
def checkBaseFolder():
    progress("-- Create temp folder for backup or restore...")
    if not os.path.exists(workingDir):os.mkdir(workingDir)
    if not os.path.exists(dumpDirPath):os.mkdir(dumpDirPath)
    progress("done")

#####################
#                   #
#     Backup        #
#                   #
#####################
if args.backup:
    progress("Script executed in restore mode the "+str(currentDate))
    ##Script execution
    #if no db in dbList, trying to collect all db available with this credentials and this host
    if not any(dbList):
        if not dbpassword:
            progress("!- No database information available, switching file only")
        else:
            progress("!- No database specified, trying to search with credentials")
            #retrieve all databases
            databaseList = subprocess.check_output("mariadb -u "+dbadmin+" -p"+dbpassword+" -sN -e 'show databases'", shell=True).decode().split("\n")
            cleanDatabaseList = []
            for database in databaseList:
                if database not in excludedDbs:
                    if database:
                        cleanDatabaseList.append(database)
                        progress("-- Found :"+database)
                        progress("done")
            dbList = cleanDatabaseList 
    if args.env=="dev" or args.env=="prod":
        progress("Starting backup of "+servicename+" to S3 "+args.env)
        checkBaseFolder()
        #backup all dbs
        if dbpassword:
            for db in dbList:
                progress("-- Backup database "+db+" to the temp folder...")
                os.system("mariadb-dump -u "+dbadmin+" -p"+dbpassword+" -h "+dbhost+" --complete-insert --routines --triggers --single-transaction \""+db+"\" > "+dumpDirPath+"/\""+db+"\".sql")
                progress("done")
        if pathsList:
            for path in pathsList:
                progress("-- Copy all content from "+path+" to the temp folder...")
                os.system("cp -r "+path+" "+dumpDirPath+"/")
                progress("done")
        progress("-- Compress all files in the temp folder")
        os.system("tar -C "+dumpDirPath+" -czf "+workingDir +"/backup.tar.gz .")
        progress("done")
        #push to s3
        for priority, region in regionS3.items():
            progress("-- Upload compressed archive to "+region+" on s3://"+servicename+"-backup-"+priority+"...")
            os.system("s3cmd -q -c "+scriptsDir+"s3.cfg --host="+region+" --access_key="+s3AccessKey+" --secret_key="+s3SecretKey+" put "+workingDir +"/backup.tar.gz s3://"+servicename+"-backup-"+priority+"/"+pathDate+"/")
            os.system("s3cmd -q -c "+scriptsDir+"s3.cfg --host="+region+" --access_key="+s3AccessKey+" --secret_key="+s3SecretKey+" del -r s3://"+servicename+"-backup-"+priority+"/latest/")
            os.system("s3cmd -q -c "+scriptsDir+"s3.cfg --host="+region+" --access_key="+s3AccessKey+" --secret_key="+s3SecretKey+" cp -r s3://"+servicename+"-backup-"+priority+"/"+pathDate+"/ s3://"+servicename+"-backup-"+priority+"/latest/")
            progress("done")
            progress("-- S3 Cleanup on "+region+"...")
            folderList = os.popen("s3cmd -q -c "+scriptsDir+"s3.cfg --host="+region+" --access_key="+s3AccessKey+" --secret_key="+s3SecretKey+" ls s3://"+servicename+"-backup-"+priority+"/ | awk '{print $NF}'").read().splitlines()
            for folder in folderList:
                if folder != "s3://"+servicename+"-backup-"+priority+"/latest/":
                    folderDate = (folder.split('/'))[3]
                    folderDate = datetime.strptime(folderDate,'%Y%m%d-%H%M%S')
                    if (datetime.timestamp(currentDate) - datetime.timestamp(folderDate)) > (retentionDays * 24 * 60 * 60):
                        progress('-- removing folder: ' +folder)
                        os.system("s3cmd -c --force "+scriptsDir+"s3.cfg --host="+region+" --access_key="+s3AccessKey+" --secret_key="+s3SecretKey+" del -r "+folder)
            progress("done")
    else:
        progress("-- Please specify destination prod or dev")
#####################
#                   #
#     Restore       #
#                   #
#####################
elif args.restore:
    progress("Script executed in restore mode the "+str(currentDate))
    #test if date specified can be restored
    if not (os.popen("s3cmd -c "+scriptsDir+"s3.cfg --host="+regionS3.get("primary")+" --access_key="+s3AccessKey+" --secret_key="+s3SecretKey+" ls s3://"+servicename+"-backup-primary/"+args.date+" | awk '{print $NF}'").read().splitlines()):
        progress("!- The date you specified does not exist on storage, please verify with --show command")
        exit()
    if args.env=="dev" or args.env=="prod":
        progress("Starting restore from "+args.date+" folder in S3 backup")
        checkBaseFolder()
        progress("-- Download backup to temp folder...")
        os.system("s3cmd -q -c "+scriptsDir+"s3.cfg --host="+regionS3.get("primary")+" --access_key="+s3AccessKey+" --secret_key="+s3SecretKey+" get s3://"+servicename+"-backup-primary/"+args.date+"/backup.tar.gz "+workingDir )
        progress("done")
        progress("-- Uncompress the backup to temp folder...")
        os.system("tar -xzf "+workingDir+"/backup.tar.gz -C "+dumpDirPath+"/.")
        progress("done")
        progress("Restore files")
        if pathsList:
            for path in pathsList:
                #rm -rf / protection
                if len(path+"/*") > 2:  
                    os.system('rm -rf '+path+'/*')
                    progress("-- Get target folder folder security settings for "+path+" ... ")
                    pathInfo = Path(path)
                    owner = pathInfo.owner()
                    group = pathInfo.group()
                    progress("done")
                    progress("-- Move restored file to "+path+" and restore security settings...")
                    os.system('mv '+dumpDirPath+'/'+os.path.basename(os.path.normpath(path))+'/* '+path+'/')
                    os.system('chown -R '+owner+':'+group+' '+path)
                    progress("done")
        else:
            progress("!- Aucun fichier présent dans le fichier de restoration")
        if not dbList:
            for dirpath, dirnames, filenames in os.walk(dumpDirPath):
                for filename in filenames:
                    if filename.endswith(".sql"):
                        dbList.append(filename.split(".")[0])
        if dbList:
            for db in dbList:
                progress("Restore database")
                progress("-- Drop database before restoring data...")
                os.system('mariadb-admin -s -u'+dbadmin+' -p'+dbpassword+' -h '+dbhost+' -f drop '+db+" > /dev/null")
                progress("done")
                progress("-- Restore database from backup...")
                os.system('mariadb-admin -s -u'+dbadmin+' -p'+dbpassword+' -h '+dbhost+' -f create '+db)
                os.system('mariadb -u'+dbadmin+' -p'+dbpassword+' -h '+dbhost+' -D '+db+' < '+dumpDirPath+'/'+db+'.sql')
                progress("done")
        else:
            progress("!- Aucune base de donnée présente dans le fichier de restoration")
        if args.extra:
            progress("-- executing post-restore script")
            progress("done")
            os.system(args.extra+" > /dev/null")
#####################
#                   #
#    Show           #
#                   #
#####################
elif args.show:
    if args.env=="dev" or args.env=="prod":
        progress("List of available restoration points on : "+regionS3.get("primary"))
        restorePoints = os.popen("s3cmd -c "+scriptsDir+"s3.cfg --host="+regionS3.get("primary")+" --access_key="+s3AccessKey+" --secret_key="+s3SecretKey+" ls s3://"+servicename+"-backup-primary/ | awk '{print $NF}'").read().splitlines()
        for point in restorePoints:
            progress("-> "+(point.split('/'))[3])
        exit()
    else:
        progress("!- Please specify S3 envirronement prod or dev")
        exit()

progress("-- Local Cleanup...")
os.system("rm -rf "+workingDir)
progress("done")
end_time = time.time()
total_time= f"{end_time - start_time:.2f}"
progress("Execution finished -> Duration : "+total_time+" seconds")