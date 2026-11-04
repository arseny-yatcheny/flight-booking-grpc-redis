set -eu
master_address=$(getent hosts redis-master | awk 'NR == 1 {print $1}')
test -n "$master_address"
cat > /tmp/sentinel.conf <<EOF
port 26379
bind 0.0.0.0
protected-mode no
dir /tmp
sentinel monitor flights $master_address 6379 2
sentinel down-after-milliseconds flights 3000
sentinel failover-timeout flights 10000
sentinel parallel-syncs flights 1
EOF
exec redis-server /tmp/sentinel.conf --sentinel
