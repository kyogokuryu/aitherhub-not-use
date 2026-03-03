#!/bin/bash
# vm_health_check.sh - Self-healing health check for AitherHub worker VM
# Runs via cron every 5 minutes to ensure worker stability
# 
# Checks:
# 1. Worker process is running
# 2. Disk space is sufficient
# 3. Memory is not critically low
# 4. Worker log is being updated (not hung)

LOG_FILE="/var/www/aitherhub/health_check.log"
WORKER_LOG="/var/www/aitherhub/worker.log"
MAX_LOG_SIZE=$((500 * 1024 * 1024))  # 500MB max log size
DISK_THRESHOLD=90  # Restart cleanup if disk usage exceeds 90%
MEMORY_THRESHOLD=95  # Alert if memory usage exceeds 95%
WORKER_STALE_MINUTES=15  # If worker log hasn't been updated in 15 min, restart

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [HEALTH] $1" >> "$LOG_FILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [HEALTH] $1"
}

# --- 1. Check and rotate worker log ---
if [ -f "$WORKER_LOG" ]; then
    LOG_SIZE=$(stat -c%s "$WORKER_LOG" 2>/dev/null || echo 0)
    if [ "$LOG_SIZE" -gt "$MAX_LOG_SIZE" ]; then
        log "Worker log is ${LOG_SIZE} bytes (>${MAX_LOG_SIZE}). Rotating..."
        cp "$WORKER_LOG" "${WORKER_LOG}.$(date +%Y%m%d_%H%M%S).bak"
        truncate -s 0 "$WORKER_LOG"
        log "Worker log rotated"
    fi
fi

# --- 2. Check disk space ---
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt "$DISK_THRESHOLD" ]; then
    log "WARN: Disk usage at ${DISK_USAGE}% (threshold: ${DISK_THRESHOLD}%)"
    
    # Emergency cleanup: remove old uploaded videos and output files
    BATCH_DIR="/var/www/aitherhub/worker/batch"
    
    # Remove uploaded videos older than 2 hours
    find "${BATCH_DIR}/uploadedvideo" -type f -mmin +120 -delete 2>/dev/null
    log "Cleaned uploaded videos older than 2 hours"
    
    # Remove output files older than 4 hours
    find "${BATCH_DIR}/output" -type f -mmin +240 -delete 2>/dev/null
    log "Cleaned output files older than 4 hours"
    
    # Remove split video files older than 2 hours
    find "${BATCH_DIR}/splitvideo" -type f -mmin +120 -delete 2>/dev/null
    log "Cleaned split video files older than 2 hours"
    
    # Remove artifacts older than 24 hours
    find "${BATCH_DIR}/artifacts" -type f -mmin +1440 -delete 2>/dev/null
    log "Cleaned artifacts older than 24 hours"
    
    # Remove old backup logs
    find /var/www/aitherhub -name "worker.log.*.bak" -mmin +1440 -delete 2>/dev/null
    
    NEW_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
    log "Disk usage after cleanup: ${NEW_USAGE}%"
fi

# --- 3. Check memory ---
MEM_TOTAL=$(free | awk '/Mem:/ {print $2}')
MEM_AVAIL=$(free | awk '/Mem:/ {print $7}')
MEM_USED_PCT=$(( (MEM_TOTAL - MEM_AVAIL) * 100 / MEM_TOTAL ))
if [ "$MEM_USED_PCT" -gt "$MEMORY_THRESHOLD" ]; then
    log "WARN: Memory usage at ${MEM_USED_PCT}% (threshold: ${MEMORY_THRESHOLD}%)"
    
    # Drop caches to free memory
    sync
    echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1
    log "Dropped filesystem caches"
fi

# --- 4. Check worker process ---
if ! systemctl is-active --quiet simple-worker; then
    log "ERROR: simple-worker is not running! Restarting..."
    sudo systemctl restart simple-worker
    sleep 5
    if systemctl is-active --quiet simple-worker; then
        log "simple-worker restarted successfully"
    else
        log "CRITICAL: simple-worker failed to restart!"
    fi
else
    log "OK: simple-worker is running (PID: $(pgrep -f simple_worker | head -1))"
fi

# --- 5. Check if worker is hung (log not updated) ---
if [ -f "$WORKER_LOG" ]; then
    LAST_MOD=$(stat -c%Y "$WORKER_LOG" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    STALE_SECONDS=$(( WORKER_STALE_MINUTES * 60 ))
    
    if [ $(( NOW - LAST_MOD )) -gt "$STALE_SECONDS" ]; then
        # Check if there are active jobs (worker might be legitimately idle)
        QUEUE_MSG_COUNT=$(python3 -c "
from azure.storage.queue import QueueClient
from dotenv import load_dotenv
import os
load_dotenv('/var/www/aitherhub/.env')
c = QueueClient.from_connection_string(os.environ['AZURE_STORAGE_CONNECTION_STRING'], os.environ.get('AZURE_QUEUE_NAME', 'video-jobs'))
props = c.get_queue_properties()
print(props.approximate_message_count)
" 2>/dev/null || echo "0")
        
        if [ "$QUEUE_MSG_COUNT" -gt "0" ]; then
            log "WARN: Worker log stale for ${WORKER_STALE_MINUTES}+ min but queue has ${QUEUE_MSG_COUNT} messages. Restarting worker..."
            sudo systemctl restart simple-worker
            sleep 5
            log "Worker restarted due to stale log with pending messages"
        else
            log "OK: Worker idle (no messages in queue, log stale is expected)"
        fi
    fi
fi

# --- 6. Rotate health check log ---
if [ -f "$LOG_FILE" ]; then
    HC_SIZE=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$HC_SIZE" -gt 10485760 ]; then  # 10MB
        tail -1000 "$LOG_FILE" > "${LOG_FILE}.tmp"
        mv "${LOG_FILE}.tmp" "$LOG_FILE"
    fi
fi

log "Health check complete. Disk: ${DISK_USAGE}%, Memory: ${MEM_USED_PCT}%"
