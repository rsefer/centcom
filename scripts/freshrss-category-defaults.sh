#!/bin/sh
# Turn on FreshRSS's "mark as read if an identical title/GUID already exists in
# the top n newest articles of the category" for every category of every user.
# Only fills in categories that do not have the setting yet, so a value you set
# by hand is kept, and new categories are picked up the next time this runs
# (reload.sh runs it after FreshRSS is healthy).
#
#   CATEGORY_DEDUP_N=100  how many newest articles to compare against
#   DRY_RUN=1             report what would change without writing

set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

docker compose exec -T -u www-data \
	-e CATEGORY_DEDUP_N="${CATEGORY_DEDUP_N:-100}" -e DRY_RUN="${DRY_RUN:-}" \
	rss php <<'PHP'
<?php
require '/var/www/FreshRSS/cli/_cli.php';
$n = (int) getenv('CATEGORY_DEDUP_N');
if ($n < 1) {
    fwrite(STDERR, "CATEGORY_DEDUP_N must be a positive number\n");
    exit(1);
}
$dry = getenv('DRY_RUN') === '1';
$keys = ['read_when_same_title_in_category', 'read_when_same_guid_in_category'];
$users = FreshRSS_user_Controller::listUsers();
if (!$users) {
    fwrite(STDERR, "No FreshRSS users found\n");
    exit(1);
}
$changedTotal = 0;
foreach ($users as $user) {
    cliInitUser($user);
    $dao = FreshRSS_Factory::createCategoryDao();
    foreach ($dao->listCategories(false) as $category) {
        $missing = array_values(array_filter($keys, fn($k) => !$category->hasAttribute($k)));
        if (!$missing) {
            continue;
        }
        foreach ($missing as $key) {
            $category->_attribute($key, $n);
        }
        echo ($dry ? 'Would set' : 'Setting'), " identical title/GUID (n=$n) on $user / ", $category->name(), "\n";
        $changedTotal++;
        if ($dry) {
            continue;
        }
        $saved = $dao->updateCategory($category->id(), [
            'name' => $category->name(),
            'kind' => $category->kind(),
            'attributes' => $category->attributes(),
        ]);
        if ($saved === false) {
            fwrite(STDERR, "Failed to update category " . $category->name() . "\n");
            exit(1);
        }
    }
}
echo $changedTotal === 0 ? "All categories already have the setting\n" : '';
PHP
