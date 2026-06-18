#!/bin/bash

CRON_SCHEDULE="*/3 * * * *"
CRON_COMMAND="~/centcom/refresh-rss.sh"
CRON_JOB="$CRON_SCHEDULE $CRON_COMMAND"
REBOOT_COMMAND="~/centcom/boot-recover.sh >> ~/centcom/boot-recover.log 2>&1"
REBOOT_JOB="@reboot $REBOOT_COMMAND"

if crontab -l 2>/dev/null | grep -F "$CRON_COMMAND" > /dev/null; then
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
