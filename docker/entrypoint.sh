#!/bin/sh
# Container entrypoint.
#
# nextime must not run as root. A data directory mounted from outside carries the rights of the host,
# and those rarely match the user inside the image by chance. So the container briefly fixes the rights
# as root and then drops to the user "nextime". PUID and PGID say which host user owns the files.

set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# A number, and not root's: PUID=0 is a common way out of rights trouble, and would run nextime as root.
for value in "$PUID" "$PGID"; do
    case "$value" in
        ''|*[!0-9]*)
            echo "nextime: PUID and PGID must be numbers (got '$PUID' and '$PGID'). Nothing has been started." >&2
            exit 1
            ;;
    esac
done
if [ "$PUID" = "0" ] || [ "$PGID" = "0" ]; then
    echo "nextime: PUID and PGID must not be 0: nextime does not run as root. Use the ids of the user who owns" >&2
    echo "  your data directory ('id' on the host shows yours). Nothing has been started." >&2
    exit 1
fi

# A port nextime can listen on; checked before anything else happens, with the values it takes.
PORT=${NEXTIME_PORT:-8000}
case "$PORT" in
    ''|*[!0-9]*) PORT_OK=no ;;
    *) if [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; then PORT_OK=yes; else PORT_OK=no; fi ;;
esac
if [ "$PORT_OK" != "yes" ]; then
    echo "nextime: NEXTIME_PORT has the value '$PORT', which is not valid. Allowed: a whole number from 1 to 65535." >&2
    echo "  Nothing has been started." >&2
    exit 1
fi

# Port inside the container, default 8000. NEXTIME_PORT is for host networking, where the container's port
# is the server's port. Docker does not expand variables in the JSON form of CMD, hence here.
if [ "$1" = "uvicorn" ]; then
    case " $* " in
        *" --port "*) ;;
        *) set -- "$@" --port "$PORT" ;;
    esac
fi

# Started without root already ("user:" in the compose file): nothing to fix.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

if [ "$(id -g nextime)" != "$PGID" ]; then
    groupmod -o -g "$PGID" nextime
fi
if [ "$(id -u nextime)" != "$PUID" ]; then
    usermod -o -u "$PUID" nextime
fi

mkdir -p /data

# Only touch it when the owner is wrong. A "chown -R" on every start costs time on large directories.
if [ "$(stat -c %u /data)" != "$PUID" ] || [ "$(stat -c %g /data)" != "$PGID" ]; then
    echo "nextime: adjusting ownership of the data directory to $PUID:$PGID."
    chown -R "$PUID:$PGID" /data
fi

# Owner does not mean writable: on a NAS the directory can belong to the right user and still be closed by
# an access list. Then a long Python error would appear mid-start that nobody reads the cause from. So we
# really write here, as the user that does it later.
if ! gosu nextime sh -c 'touch /data/.write-test' 2>/dev/null; then
    echo "nextime: the data directory is not writable." >&2
    echo "" >&2
    echo "  nextime runs as uid $PUID, gid $PGID and cannot write to the" >&2
    echo "  directory mounted at /data. Nothing has been started." >&2
    echo "" >&2
    echo "  On the host, that directory needs to belong to that user:" >&2
    echo "" >&2
    echo "      sudo chown -R $PUID:$PGID /path/to/your/data" >&2
    echo "      sudo chmod -R u+rwX /path/to/your/data" >&2
    echo "" >&2
    echo "  PUID and PGID are set in your compose file. To find your own," >&2
    echo "  run 'id' on the host and use the uid and gid it reports." >&2
    exit 1
fi
rm -f /data/.write-test

exec gosu nextime "$@"
