#!/system/bin/sh
PID=$(cat /data/data/com.siyi.dshapp/files/engine.pid 2>/dev/null)
LOG=/storage/emulated/0/思忆集/app/sif/evidence/restart.log
if [ -z "$PID" ]; then echo "$(date) no pid file" >> "$LOG"; exit 1; fi
echo "$(date) 计划重启 engine pid=$PID，等待 30 秒" >> "$LOG"
sleep 30
kill -9 "$PID" 2>>"$LOG"
echo "$(date) 已发送 SIGKILL 给 $PID, rc=$?" >> "$LOG"
