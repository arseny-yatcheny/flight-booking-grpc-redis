set -eu
master_address=$(getent hosts redis-master | awk 'NR == 1 {print $1}')
test -n "$master_address"
exec redis-server --replicaof "$master_address" 6379 --save '' --appendonly no --protected-mode no
