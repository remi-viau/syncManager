# Backup and Restore Script for docker and S3

## Overview
This script provides a solution for automating the backup and restoration of static data folders and service databases inside a docker container to/from an S3 bucket. It supports multiple environments (e.g., `dev` and `prod`) and includes options for viewing available restore points.

## Features
- **Backup**: Compresses specified directories and databases, uploads them to OVH S3, and manages retention policies.
- **Restore**: Downloads and restores the latest or specified backups from S3.
- **Show**: Lists available restore points.
- **Configuration**: Uses a configuration file and environment variables for flexible setup.

## Prerequisites
- Python 3.6 or higher
- Access to S3 with credentials
- Two "production" buckets on S3 named {service}-backup-primary on first region and {service}-backup-secondary on second region
- Two "development" buckets on S3 named {service}-backup-primary-dev on first region and {service}-backup-secondary-dev on second region
- Installed dependencies:
  - `s3cmd`
  - `mariadb` (client and admin tools)
  - `tar`

## Usage
Run the script with `docker exec -it -u root` or any suitable Docker container execution method.
Add it to a local cron via `docker exec -t -u root` to schedule your container backups

### Command Options
| Flag             | Description                                                                 |
|------------------|-----------------------------------------------------------------------------|
| `--backup`       | Creates a backup and uploads it to S3.                                      |
| `--restore`      | Restores the latest or specified backup from S3.                           |
| `--show`         | Displays available restore points from the selected environment.            |
| `--env`          | Specifies the environment (`dev` or `prod`). **Required**.                 |
| `--date`         | Specifies the date folder to restore (default: `latest`).                 |
| `--extra`        | Path to an additional script to execute after restoration.                |

### Example Commands
1. **Backup data for `prod` environment**:
   ```bash
   docker exec -it -u root {service} syncManager.py --backup --env prod
   ```

2. **Restore data for `dev` environment**:
   ```bash
   docker exec -it -u root {service} syncManager.py --restore --env dev --date 20231121-120000
   ```

3. **Show available restore points for `prod` environment**:
   ```bash
   docker exec -it -u root {service} syncManager.py --show --env prod
   ```

## Configuration
The script reads settings from `syncManager.ini` in the script directory. Update the file with your service name, paths to backup, databases, and S3 credentials.
Or use env var listed : DATABASE_USERNAME, DATABASE_PASSWORD, DATABASE_HOST, S3_BACKUP_ACCESS_KEY, S3_BACKUP_SECRET_KEY, S3_BACKUP_ACCESS_KEY_DEV, S3_BACKUP_SECRET_KEY_DEV


### `syncManager.ini` Example
```ini
[info]
servicename = your-service-name

[pathListTobackup]
path = /path/to/dir1,/path/to/dir2

[dbListTobackup]
db = db1,db2

[databaseCredentials]
dbadmin = admin_user
dbpassword = admin_password
dbhost = localhost

[s3Credentials]
s3AccessKey = access_key_prod
s3SecretKey = secret_key_prod
s3AccessKeyDev = access_key_dev
s3SecretKeyDev = secret_key_dev

[regionS3]
primary = your-primary-region
secondary = your-secondary-region

[backupSettings]
retentionDays = 30
```

## Logging
The script uses color-coded terminal messages to indicate progress:
- **Green**: Success
- **Purple**: General information
- **Yellow**: Warnings
- **Red**: Errors

## Cleanup
Temporary files are automatically deleted after each operation and folders older than retention date purged on S3

## Notes
- Ensure proper permissions for reading/writing paths and accessing S3.
- Retention policy is managed by comparing timestamps in S3.

## Author
Written by: **Rémi Viau**  
Version: **3.1**  
Date: **21-11-2024**
