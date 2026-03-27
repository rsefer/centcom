#!/bin/sh

set -e
ssh centcom "cd ~/centcom && git pull && ./reload.sh"
