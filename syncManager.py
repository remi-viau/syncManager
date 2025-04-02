#!/usr/bin/python3
#    _____                  __  __
#   / ____|                |  \/  |
#  | (___  _   _ _ __   ___| \  / | __ _ _ __   __ _  __ _  ___ _ __
#   \___ \| | | | '_ \ / __| |\/| |/ _` | '_ \ / _` |/ _` |/ _ \ '__|
#   ____) | |_| | | | | (__| |  | | (_| | | | | (_| | (_| |  __/ |
#  |_____/ \__, |_| |_|\___|_|  |_|\__,_|_| |_|\__,_|\__, |\___|_|
#           __/ |                                     __/ |
#          |___/                                     |___/
# Version 3.2 - 02 - 04 - 2025
# Written by: Rémi Viau
#
# DESCRIPTION:
# This script manages backup and restoration of static data folders and service databases
# to/from OVH S3 buckets. It supports both development and production environments with
# separate credentials and bucket naming conventions.
#
# FEATURES:
# - Backup: Creates compressed archives of specified paths and databases, uploads to S3
# - Restore: Downloads and restores from specified or latest backup
# - Show: Lists available restore points from S3
# - Environment support: dev (primary-dev) and prod (primary/secondary)
# - Automatic cleanup of old backups based on retention period
#
# USAGE:
# Run with docker exec -it -u root <container_name> python3 syncManager.py [options]
# Options:
#   --backup        Create and upload a backup
#   --restore       Restore from a backup
#   --show          List available restore points
#   --env [dev|prod] Environment to work with (required)
#   --date [YYYYMMDD-HHMMSS] Specific backup date to restore (default: latest)
#   --extra [path]  Path to additional script to run after restore
#
# CONFIGURATION:
# Requires syncManager.ini in the same directory with sections:
# [info] servicename
# [pathListTobackup] path
# [dbListTobackup] db
# [databaseCredentials] dbadmin, dbpassword, dbhost
# [s3Credentials] s3AccessKey, s3SecretKey, s3AccessKeyDev, s3SecretKeyDev
# [regionS3] primary, secondary
# [backupSettings] retentionDays
#
# DEPENDENCIES:
# - Python 3.x
# - s3cmd
# - mariadb/mariadb-dump
# - tar
#
# NOTES:
# - Must be run as root for proper file permissions
# - S3 buckets must be pre-created: servicename-backup-[primary|primary-dev|secondary]
# - Uses environment variables as override for config file values

import os
import sys
import time
import shutil
import subprocess
import argparse
import configparser
from datetime import datetime
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Argument parsing
parser = argparse.ArgumentParser(description='Backup and restore WordPress content and data to/from S3')
parser.add_argument('--backup', action='store_true', help='Create a backup and upload it to S3')
parser.add_argument('--restore', action='store_true', help='Restore the latest backup from S3')
parser.add_argument('--show', action='store_true', help='Show available restore points')
parser.add_argument('--env', help='Environment to backup/restore (dev or prod)', required=True)
parser.add_argument('--date', help='Date to restore (default: latest)', default="latest")
parser.add_argument('--extra', help='Path to extra script to run after restore')
args = parser.parse_args()

# Load configuration
config = configparser.ConfigParser()
config.read(os.path.dirname(os.path.realpath(__file__)) + '/syncManager.ini')

# Utility functions
def get_value(env_var, config_section, config_key, as_list=False):
    value = os.getenv(env_var) or config.get(config_section, config_key)
    return value.split(',') if as_list else value

# Script configuration
scriptsDir = os.path.dirname(os.path.realpath(__file__)) + "/"
servicename = config.get("info", "servicename")
pathsList = get_value('PATH_LIST', 'pathListTobackup', 'path', as_list=True)
dbList = get_value('DATABASE_NAME', 'dbListTobackup', 'db', as_list=True)
dbadmin = get_value('DATABASE_USERNAME', 'databaseCredentials', 'dbadmin')
dbpassword = get_value('DATABASE_PASSWORD', 'databaseCredentials', 'dbpassword')
dbhost = get_value('DATABASE_HOST', 'databaseCredentials', 'dbhost')
s3AccessKey = get_value('S3_BACKUP_ACCESS_KEY', 's3Credentials', 's3AccessKey')
s3SecretKey = get_value('S3_BACKUP_SECRET_KEY', 's3Credentials', 's3SecretKey')
s3AccessKeyDev = get_value('S3_BACKUP_ACCESS_KEY_DEV', 's3Credentials', 's3AccessKeyDev')
s3SecretKeyDev = get_value('S3_BACKUP_SECRET_KEY_DEV', 's3Credentials', 's3SecretKeyDev')
retentionDays = config.getint("backupSettings", "retentionDays", fallback=30)

# Environment-specific configuration
if args.env == "dev":
    s3AccessKey = s3AccessKeyDev
    s3SecretKey = s3SecretKeyDev
    regionS3 = {"primary-dev": config.get("regionS3", "primary")}
else:
    regionS3 = {
        "primary": config.get("regionS3", "primary"),
        "secondary": config.get("regionS3", "secondary")
    }

# Internal variables
workingDir = scriptsDir + 'temp/'
currentDate = datetime.now()
pathDate = currentDate.strftime('%Y%m%d-%H%M%S')
dumpDirPath = workingDir + pathDate
excludedDbs = ['mysql', 'information_schema', 'performance_schema', 'sys']
start_time = time.time()

class bcolors:
    DEFAULT = '\033[97m'
    OKGREEN = "\033[92m"
    HEADER = "\033[95m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"

def progress(text):
    if text == "done":
        logger.info(f"{bcolors.OKGREEN} ✔{bcolors.DEFAULT}")
    elif text.startswith("!"):
        logger.warning(f"{bcolors.WARNING}{text}{bcolors.DEFAULT}")
    elif text.startswith("+"):
        logger.error(f"{bcolors.FAIL}{text}{bcolors.DEFAULT}")
    elif text.startswith("-"):
        print(f"{text}", end="", flush=True)
    else:
        logger.info(f"{bcolors.HEADER}{text}{bcolors.DEFAULT}")

def test_vars():
    mandatory_vars = [
        ("Service name", servicename),
        ("S3 Access key", s3AccessKey),
        ("S3 Secret key", s3SecretKey),
    ]
    errors = [f"{name} not found" for name, value in mandatory_vars if not value]
    if errors:
        raise ValueError("\n".join(errors))

def check_base_folder():
    progress("-- Create temp folder for backup or restore...")
    os.makedirs(dumpDirPath, exist_ok=True)
    progress("done")

def cleanup():
    if os.path.exists(workingDir):
        shutil.rmtree(workingDir, ignore_errors=True)

# Backup
if args.backup:
    progress(f"Script executed in backup mode on {currentDate}")
    try:
        test_vars()
        if args.env not in ["dev", "prod"]:
            progress("!- Please specify destination prod or dev")
            sys.exit(1)

        if not dbList and dbpassword:
            progress("!- No database specified, searching with credentials")
            db_list_cmd = f"mariadb -u {dbadmin} -p{dbpassword} -sN -e 'show databases'"
            dbList = [db for db in subprocess.check_output(db_list_cmd, shell=True).decode().split("\n")
                     if db and db not in excludedDbs]

        progress(f"Starting backup of {servicename} to S3 {args.env}")
        check_base_folder()

        if dbpassword and dbList:
            for db in dbList:
                progress(f"-- Backup database {db} to temp folder...")
                os.system(f"mariadb-dump -u {dbadmin} -p{dbpassword} -h {dbhost} --complete-insert --routines --triggers --single-transaction \"{db}\" > {dumpDirPath}/\"{db}\".sql")
                progress("done")

        if pathsList:
            for path in pathsList:
                progress(f"-- Copy content from {path} to temp folder...")
                os.makedirs(f"{dumpDirPath}{path}", exist_ok=True)
                os.system(f"cp -r {path}/* {dumpDirPath}{path}/")
                progress("done")

        progress("-- Compress files in temp folder")
        os.system(f"tar -C {dumpDirPath} -czf {workingDir}/backup.tar.gz .")
        progress("done")

        for priority, region in regionS3.items():
            progress(f"-- Upload to {region} on s3://{servicename}-backup-{priority}...")
            os.system(f"s3cmd -q -c {scriptsDir}s3.cfg --host={region} --access_key={s3AccessKey} --secret_key={s3SecretKey} put {workingDir}/backup.tar.gz s3://{servicename}-backup-{priority}/{pathDate}/")
            os.system(f"s3cmd -q -c {scriptsDir}s3.cfg --host={region} --access_key={s3AccessKey} --secret_key={s3SecretKey} del -r s3://{servicename}-backup-{priority}/latest/")
            os.system(f"s3cmd -q -c {scriptsDir}s3.cfg --host={region} --access_key={s3AccessKey} --secret_key={s3SecretKey} cp -r s3://{servicename}-backup-{priority}/{pathDate}/ s3://{servicename}-backup-{priority}/latest/")
            progress("done")

            progress(f"-- S3 Cleanup on {region}...")
            folderList = os.popen(f"s3cmd -q -c {scriptsDir}s3.cfg --host={region} --access_key={s3AccessKey} --secret_key={s3SecretKey} ls s3://{servicename}-backup-{priority}/ | awk '{{print $NF}}'").read().splitlines()
            for folder in folderList:
                if folder != f"s3://{servicename}-backup-{priority}/latest/":
                    folderDate = datetime.strptime(folder.split('/')[3], '%Y%m%d-%H%M%S')
                    if (datetime.timestamp(currentDate) - datetime.timestamp(folderDate)) > (retentionDays * 24 * 60 * 60):
                        progress(f'-- removing folder: {folder}')
                        os.system(f"s3cmd -c --force {scriptsDir}s3.cfg --host={region} --access_key={s3AccessKey} --secret_key={s3SecretKey} del -r {folder}")
            progress("done")

    except Exception as e:
        progress(f"+! Backup failed: {str(e)}")
        sys.exit(1)

# Restore
elif args.restore:
    progress(f"Script executed in restore mode on {currentDate}")
    try:
        test_vars()
        if args.env not in ["dev", "prod"]:
            progress("!- Please specify environment prod or dev")
            sys.exit(1)

        first_region = regionS3[next(iter(regionS3))]
        if not os.popen(f"s3cmd -c {scriptsDir}s3.cfg --host={first_region} --access_key={s3AccessKey} --secret_key={s3SecretKey} ls s3://{servicename}-backup-{next(iter(regionS3))}/{args.date}/").read():
            progress("!- Specified date does not exist on storage, use --show to verify")
            sys.exit(1)

        progress(f"Starting restore from {args.date} folder in S3 backup")
        check_base_folder()
        progress("-- Download backup to temp folder...")
        os.system(f"s3cmd -q -c {scriptsDir}s3.cfg --host={first_region} --access_key={s3AccessKey} --secret_key={s3SecretKey} get s3://{servicename}-backup-{next(iter(regionS3))}/{args.date}/backup.tar.gz {workingDir}")
        progress("done")
        progress("-- Uncompress backup...")
        os.system(f"tar -xzf {workingDir}/backup.tar.gz -C {dumpDirPath}/.")
        progress("done")

        if pathsList:
            for path in pathsList:
                path_info = Path(path)
                owner, group = path_info.owner(), path_info.group()
                progress(f"-- Restore files to {path}...")
                os.system(f'rm -rf {path}/* && mv {dumpDirPath}{path}/* {path} && chown -R {owner}:{group} {path}')
                progress("done")

        if not dbList:
            dbList = [f.split(".")[0] for _, _, files in os.walk(dumpDirPath) for f in files if f.endswith(".sql")]

        if dbList:
            for db in dbList:
                progress(f"-- Restore database {db}...")
                os.system(f'mariadb-admin -s -u{dbadmin} -p{dbpassword} -h {dbhost} -f drop {db} > /dev/null')
                os.system(f'mariadb-admin -s -u{dbadmin} -p{dbpassword} -h {dbhost} -f create {db}')
                os.system(f'mariadb -u{dbadmin} -p{dbpassword} -h {dbhost} -D {db} < {dumpDirPath}/{db}.sql')
                progress("done")

        if args.extra:
            progress("-- Executing post-restore script...")
            os.system(f"{args.extra} > /dev/null")
            progress("done")

    except Exception as e:
        progress(f"+! Restore failed: {str(e)}")
        sys.exit(1)

# Show
elif args.show:
    try:
        if args.env in ["dev", "prod"]:
            first_region_key = next(iter(regionS3))
            region = regionS3[first_region_key]
            bucket_suffix = "primary-dev" if args.env == "dev" else "primary"
            
            progress(f"List of available restoration points on: {region}")
            cmd = f"s3cmd -q -c {scriptsDir}s3.cfg --host={region} --access_key={s3AccessKey} --secret_key={s3SecretKey} ls s3://{servicename}-backup-{bucket_suffix}/ | awk '{{print $NF}}'"
            restorePoints = os.popen(cmd).read().splitlines()
            
            for point in restorePoints:
                progress("-> " + point.split('/')[3])
            sys.exit(0)
        else:
            progress("!- Please specify S3 environment prod or dev")
            sys.exit(1)
    except Exception as e:
        progress(f"+! Show failed: {str(e)}")
        sys.exit(1)

# Cleanup and timing
progress("-- Local Cleanup...")
cleanup()
progress("done")
total_time = f"{time.time() - start_time:.2f}"
progress(f"Execution finished -> Duration: {total_time} seconds")
