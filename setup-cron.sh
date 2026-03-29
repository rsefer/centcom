#!/bin/bash

CRON_SCHEDULE="*/3 * * * *"
CRON_COMMAND="~/centcom/refresh-rss.sh"
CRON_JOB="$CRON_SCHEDULE $CRON_COMMAND"

if crontab -l 2>/dev/null | grep -F "$CRON_COMMAND" > /dev/null; then
	echo "✓ Cron job already exists. Skipping."
	exit 0
fi

(crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -

if [ $? -eq 0 ]; then
	echo "✓ Cron job successfully added!"
	echo "Job: $CRON_JOB"
	crontab -l | grep "$CRON_COMMAND"
else
	echo "✗ Failed to add cron job"
	exit 1
fi
