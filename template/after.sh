# Wait for the proxy (and therefore the session) to be ready.
# The proxy starts immediately, so this should succeed quickly even
# while the model is still loading.
echo "Waiting for the llama Web UI proxy to open port ${port}..."
echo "TIMING - Starting wait at: $(date)"

WAIT_PORT=120
python3 -c "
import socket, sys, time
port = ${port}
timeout = $WAIT_PORT
start = time.time()
while time.time() - start < timeout:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(('127.0.0.1', port))
        s.close()
        if result == 0:
            print(f'Port {port} opened')
            sys.exit(0)
    except Exception:
        pass
    time.sleep(1)
print(f'Timed out waiting for port {port} after {timeout}s')
sys.exit(1)
"

if [ $? -eq 0 ]; then
    echo "Discovered Web UI proxy listening on port ${port}!"
    echo "TIMING - Wait ended at: $(date)"
else
    echo "Timed out waiting for the Web UI proxy to open port ${port}!"
    echo "TIMING - Wait ended at: $(date)"
    pkill -P ${SCRIPT_PID}
    clean_up 1
fi
sleep 2
