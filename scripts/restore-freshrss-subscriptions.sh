#!/bin/sh

set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

docker compose exec -T -u www-data rss php <<'PHP'
<?php
require '/var/www/FreshRSS/cli/_cli.php';
$users = FreshRSS_user_Controller::listUsers();
if (!$users) {
    fwrite(STDERR, "No FreshRSS users found\n");
    exit(1);
}
foreach ($users as $user) {
    cliInitUser($user);
    if ((FreshRSS_Context::systemConf()->db['type'] ?? '') !== 'sqlite') {
        fwrite(STDERR, "Only SQLite restores are supported\n");
        exit(1);
    }
    if (!is_file(DATA_PATH . '/opml-' . $user . '.xml')) {
        fwrite(STDERR, "A user's OPML backup is missing\n");
        exit(1);
    }
    $pdo = new PDO('sqlite:' . USERS_PATH . '/' . $user . '/db.sqlite');
    $tables = $pdo->query("SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")->fetchColumn();
    if ((int) $tables !== 0) {
        fwrite(STDERR, "Refusing to initialize a non-empty user database\n");
        exit(1);
    }
}
foreach ($users as $user) {
    cliInitUser($user);
    if (!FreshRSS_Factory::createUserDao($user)->createUser()) {
        fwrite(STDERR, "FreshRSS schema initialization failed\n");
        exit(1);
    }
}
PHP

docker compose exec -T -u www-data rss sh -c '
	set -e
	cd /var/www/FreshRSS
	for user in $(./cli/list-users.php); do
		./cli/import-for-user.php --user "$user" --filename "data/opml-$user.xml"
	done
'
