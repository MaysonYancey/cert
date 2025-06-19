from flask import Flask, send_from_directory, request, jsonify
import json
import logging
import os
import subprocess
import time
import requests
from pathlib import Path

# Configure root logger to output debug information to stdout so that
# `docker-compose logs` will display detailed activity from this service.
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static')

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/submit', methods=['POST'])
def submit():
    """Handle final submission from the web form.

    The received JSON payload should contain `service`, `ip` and `port` keys.
    After persisting the data we perform several administration tasks:
    - append a new block to the Caddyfile
    - create a DNS record via the Cloudflare API
    - restart the local Caddy container
    - call a remote script to register the host in Pi-hole

    Extensive logging is used so that `docker compose logs` shows exactly
    what happened at each step.
    """

    # Parse JSON body; default to empty dict if decoding fails.
    data = request.get_json() or {}
    logger.debug("route success %s", data)

    name = data.get('service') or data.get('name')
    ip = data.get('ip')
    port = data.get('port')

    # Persist submitted parameters to a simple JSON file for later reference.
    record = {'name': name, 'ip': ip, 'port': port}
    services_file = Path('services.json')
    try:
        if services_file.exists():
            services = json.loads(services_file.read_text())
        else:
            services = []
    except json.JSONDecodeError:
        services = []
    services.append(record)
    services_file.write_text(json.dumps(services, indent=2))

    logger.debug('Saved parameters: %s', record)

    # ---------------------- Caddyfile manipulation -----------------------
    caddy_entry = (
        f"{name}.{{$MY_DOMAIN}} {{\n"
        f"        reverse_proxy {ip}:{port}\n"
        "        tls {\n"
        "                dns cloudflare {env.CLOUDFLARE_API_TOKEN}\n"
        "        }\n"
        "}\n"
    )
    try:
        with open('/home/mayson/containers/caddy/conf/Caddyfile', 'a') as f:
            f.write(caddy_entry)
        logger.debug('Caddy file appended.')
    except Exception as e:
        logger.error('Unable to append to Caddyfile: %s', e)

    # ------------------------- Cloudflare API ---------------------------
    token = os.getenv('CLOUDFLARE_API_TOKEN')
    payload = {
        'type': 'A',
        'name': f'{name}.yance.org',
        'content': '10.0.5.17',
        'ttl': 3600,
        'proxied': False
    }
    if not token:
        logger.error('CLOUDFLARE_API_TOKEN environment variable not set')
    else:
        try:
            resp = requests.post(
                'https://api.cloudflare.com/client/v4/zones/e9b0ab75679f16840f46204e02141c8d/dns_records',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json'
                },
                json=payload,
                timeout=10
            )
            logger.debug('Cloudflare API response: %s', resp.text)
        except Exception as e:
            logger.error('Cloudflare API call failed: %s', e)

    # Wait a moment before restarting services to give Cloudflare a chance
    # to process the request.
    time.sleep(10)

    # --------------------------- Restart Caddy --------------------------
    try:
        subprocess.run(
            ['docker', 'compose', 'restart', 'caddy'],
            cwd='/home/mayson/containers/caddy',
            check=True
        )
        logger.debug('Caddy restarted.')
    except subprocess.CalledProcessError as e:
        logger.error('Failed to restart Caddy: %s', e)

    # ------------------- Register entry on networking VM ----------------
    try:
        subprocess.run(
            ['ssh', 'mayson@10.0.5.18', '/home/mayson/scripts/add-localdns-entry.sh', name, ip],
            check=True
        )
        logger.debug('DNS entry added on networking VM.')
    except subprocess.CalledProcessError as e:
        logger.error('Remote DNS script failed: %s', e)

    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
