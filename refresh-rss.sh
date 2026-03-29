#!/bin/sh

set -e

echo $(curl -Lv "http://localhost:8080/i/?c=feed&a=actualize&ajax=1")
