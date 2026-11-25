#!/bin/bash

CRON_SCHEDULE="*/3 * * * *"
CRON_COMMAND="~/centcom/services/freshrss/refresh-rss.sh"
CRON_JOB="$CRON_SCHEDULE $CRON_COMMAND"
REBOOT_COMMAND="~/centcom/scripts/boot-recover.sh >> ~/centcom/boot-recover.log 2>&1"
REBOOT_JOB="@reboot $REBOOT_COMMAND"
BACKUP_SCHEDULE="15 3 * * *"
BACKUP_COMMAND="~/centcom/scripts/backup.sh >> ~/centcom/backup.log 2>&1"
BACKUP_JOB="$BACKUP_SCHEDULE $BACKUP_COMMAND"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")" || exit 1
python3 scripts/services.py generate || exit 1
SERVICES="$(docker compose config --services)" || exit 1

if [ -L compose.yaml ]; then
	echo "Multi-host FreshRSS uses its container's CRON_MIN schedule."
elif ! printf '%s\n' "$SERVICES" | grep -Fxq rss; then
	echo "FreshRSS is not configured on this host; skipping its cron job."
elif crontab -l 2>/dev/null | grep -F "$CRON_COMMAND" > /dev/null; then
	echo "✓ Cron job already exists. Skipping."
else
	(crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
	if [ $? -eq 0 ]; then
		echo "✓ Cron job successfully added!"
		echo "Job: $CRON_JOB"
		crontab -l | grep "$CRON_COMMAND"
	else
		echo "✗ Failed to add cron job"
		exit 1
	fi
fi

if crontab -l 2>/dev/null | grep -F "$REBOOT_COMMAND" > /dev/null; then
	echo "✓ Reboot recovery job already exists. Skipping."
else
	(crontab -l 2>/dev/null; echo "$REBOOT_JOB") | crontab -
	if [ $? -eq 0 ]; then
		echo "✓ Reboot recovery job successfully added!"
		echo "Job: $REBOOT_JOB"
		crontab -l | grep "$REBOOT_COMMAND"
	else
		echo "✗ Failed to add reboot recovery job"
		exit 1
	fi
fi

if crontab -l 2>/dev/null | grep -F "$BACKUP_COMMAND" > /dev/null; then
	echo "✓ Backup job already exists. Skipping."
else
	(crontab -l 2>/dev/null; echo "$BACKUP_JOB") | crontab -
	if [ $? -eq 0 ]; then
		echo "✓ Backup job successfully added!"
		echo "Job: $BACKUP_JOB"
		crontab -l | grep "$BACKUP_COMMAND"
	else
		echo "✗ Failed to add backup job"
		exit 1
	fi
fi
