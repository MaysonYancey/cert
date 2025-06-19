from flask import Flask, send_from_directory, request, jsonify

app = Flask(__name__, static_folder='static')

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/submit', methods=['POST'])
def submit():
    data = request.get_json() or {}
    print('route success', data)
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
