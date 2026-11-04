set -eu
cat > /tmp/sentinel.conf <<'EOF'
port 26379
bind 0.0.0.0
protected-mode no
dir /tmp
sentinel resolve-hostnames yes
sentinel announce-hostnames yes
sentinel monitor flights redis-master 6379 2
sentinel down-after-milliseconds flights 3000
sentinel failover-timeout flights 10000
sentinel parallel-syncs flights 1
EOF
exec redis-server /tmp/sentinel.conf --sentinel
