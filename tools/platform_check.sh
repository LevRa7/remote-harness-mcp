#!/system/bin/sh
# Run tools/platform_check.py inside the Termux sandbox.
#
# Copy this directory (or just the two files) to the phone, e.g. through a
# throwaway HTTP server plus `adb reverse`:
#
#   python -m http.server 8099 --bind 127.0.0.1 --directory tools &
#   adb reverse tcp:8099 tcp:8099
#   adb shell "run-as com.termux sh -c 'curl -s http://127.0.0.1:8099/platform_check.py -o \
#       /data/data/com.termux/files/home/platform_check.py'"
#   adb shell run-as com.termux sh /data/data/com.termux/files/home/platform_check.sh
#
export PREFIX=/data/data/com.termux/files/usr
export HOME=/data/data/com.termux/files/home
export PATH="$PREFIX/bin:/system/bin:/system/xbin"
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")" || exit 2
exec python3 platform_check.py
