from flask import Flask, render_template, jsonify
from xarxa import nodes, matriu_xarxa

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/topologia')
def topologia():
    noms_nodes = list(nodes.keys())
    links = []
    for i in range(len(matriu_xarxa)):
        for j in range(i+1, len(matriu_xarxa)):
            if matriu_xarxa[i][j] == 1:
                links.append({'from': noms_nodes[i], 'to': noms_nodes[j]})
    return jsonify({'nodes': nodes, 'links': links})

if __name__ == '__main__':
    app.run(debug=True)