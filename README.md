# Sistema federat de Network Digital Twins (NDT) amb suport per a XRF

Implementació d'un **sistema federat de Network Digital Twins** amb suport per a
**Extended Reality Functions (XRF)**, desenvolupat com a Treball de Fi de Grau a
l'ETSETB (UPC).

El sistema manté una rèplica sincronitzada en temps real d'una xarxa emulada amb
**Mininet** + **FRRouting**: una instància **Original** propaga cada canvi de
topologia (alta/baixa de nodes, enrutament) cap a una o més instàncies **Twin**,
que repliquen l'estat i hi executen funcions addicionals (XRF) sense afectar
l'Original. Tot s'opera des d'un dashboard web amb mètriques en temps real.

> Director: Dr. Xavier Hesselbach · Autor: Kevin Manuel Rodríguez Flores

---

## Arquitectura

Cada màquina (Original o Twin) executa el **mateix** `app.py`. El rol es decideix
per arguments de línia de comandes. Cada instància aixeca **dos servidors**:

| Port | Servidor | Funció |
|------|----------|--------|
| `5000` | Flask (HTTP / API REST) | Gestió de topologia, nodes, enrutament i proposals |
| `5001` | Flask-SocketIO (WebSocket) | Mètriques en temps real cap al dashboard (CPU, RAM, tràfic, ping del canal) |

- **Original** → propaga els canvis als Twins i en monitoritza l'estat
  (heartbeats, hash de topologia, resincronització).
- **Twin** → replica la topologia de l'Original i **a més** exposa els endpoints
  d'XRF. Els XRF estan limitats al Twin: estan condicionats per `_IS_TWIN` i no
  existeix res exclusiu de l'Original.

La sincronització Original ↔ Twin es fa via HTTP (mòdul `sync.py`), i el canal
físic entre màquines es monitoritza amb ping periòdic.

---

## Requisits

### Paquets de sistema (no s'instal·len amb pip)

- **Mininet** — emulació de xarxa (inclou **Open vSwitch** i **iperf**)
- **FRRouting (FRR)** — daemons d'enrutament (`zebra`, `ospfd`)
- Privilegis d'administrador (`sudo`): Mininet crea network namespaces

Per als XRF (només a la màquina Twin):

- **Docker**
- **Minikube** + **kubectl** — desplegament dels microserveis XRF en Kubernetes

### Dependències Python

Recollides a [`requirements.txt`](requirements.txt): Flask, Werkzeug,
Flask-SocketIO, psutil, requests, numpy, scipy i matplotlib.

---

## Instal·lació

```bash
# 1. Clona el repositori
git clone https://github.com/kevinrodri1072/TFG
cd TFG_KEVF20

# 2. (Recomanat) entorn virtual
python3 -m venv venv
source venv/bin/activate

# 3. Dependències Python
pip install -r requirements.txt
```

Mininet i FRR s'instal·len a part com a paquets de sistema. Consulteu la secció
d'instal·lació de la memòria per al detall de l'entorn de laboratori.

---

## Execució

L'aplicació requereix `sudo` perquè Mininet necessita crear interfícies i
namespaces de xarxa.

```bash
# ── Màquina ORIGINAL ──
sudo python3 app.py

# ── Màquina TWIN ──
sudo python3 app.py --twin --original-ip 10.4.39.102
```

Un cop arrencat, obriu el dashboard al navegador:

```
http://localhost:5000
```

### Arguments principals

| Argument | Descripció | Per defecte |
|----------|------------|-------------|
| `--twin` | Executa la instància com a Twin | (Original) |
| `--original-ip IP` | IP del PC Original (Twin) | `10.4.39.102` |
| `--twin-port PORT` | Port per defecte dels Twins | `5000` |

A l'arrencada el sistema neteja restes de sessions Mininet anteriors (`mn -c`),
mata daemons FRR penjats i pre-escalfa un pool de 5 routers per accelerar les
altes de nodes.

---

## Estructura del projecte

```
TFG_KEVF20/
├── app.py                  # Punt d'entrada: arrenca HTTP (5000) + WebSocket (5001)
├── xarxa.py                # Classe Xarxa: gestió de Mininet, nodes, links, FRR/OSPF
├── sync.py                 # Sincronització Original ↔ Twins (registre, heartbeat, resync)
├── utils.py                # Utilitats (parse de ping, mesura d'ample de banda amb iperf)
├── requirements.txt        # Dependències Python
│
├── routes/                 # Blueprints de l'API REST
│   ├── topology.py         #   GET /topology, /matrix, /export · POST /load_network
│   ├── nodes.py            #   POST /add_host, /add_router, /remove_node
│   ├── metrics.py          #   GET /metrics/ping, /metrics/sync, /metrics/global...
│   ├── routing.py          #   GET/POST mode d'enrutament (OSPF / Manual), rutes per router
│   ├── xrfs.py             #   Endpoints XRF (Kubernetes, només Twin)
│   └── proposals.py        #   /propose, /twin/register, /twin/heartbeat...
│
├── XRFs/                   # Extended Reality Functions (microserveis, només Twin)
│   ├── hops/               #   nombre de salts entre nodes
│   ├── neighbors/          #   veïns d'un node
│   ├── traffic/            #   tràfic per enllaç
│   └── latency_matrix/     #   matriu de latències entre nodes
│       └── (cada XRF: Dockerfile + manifest .yaml + servei Flask)
│
├── static/                 # CSS, JS i imatges del dashboard
├── templates/              # index.html (dashboard) i xrfs.html
└── plotscripts/            # Experiments i generació de gràfiques dels resultats
    ├── sync_latency_test.py    # Estudi de latència de sincronització (escalat de xarxa)
    ├── merge_runs.py           # Combina múltiples execucions
    └── export_panels.py        # Exporta panells individuals de les figures
```

---

## Extended Reality Functions (XRF)

Els XRF són microserveis desplegats en **Kubernetes** (via Minikube) que
s'executen **únicament a la màquina Twin**. Cadascun és un servei Flask
empaquetat amb el seu `Dockerfile` i el seu manifest de desplegament `.yaml`:

| XRF | Funció |
|-----|--------|
| `hops` | Nombre de salts entre dos nodes |
| `neighbors` | Veïns directes d'un node |
| `traffic` | Tràfic per enllaç |
| `latency_matrix` | Matriu de latències entre tots els nodes |

A l'arrencada, el Twin construeix automàticament les imatges Docker dels XRF
dins del context de Minikube.

---

## Experiments i resultats

El directori `plotscripts/` conté els scripts de validació. `sync_latency_test.py`
escala progressivament la xarxa i mesura, per a cada operació, la latència local,
la de xarxa, la del Twin i la latència extrem a extrem de sincronització, així com
el payload, el throughput i l'ús de CPU/RAM. Genera un CSV i una graella de 9
panells de figures, tant per al mode d'enrutament **OSPF** com **Manual**.

```bash
sudo python3 plotscripts/sync_latency_test.py
```

---

## Context

Treball de Fi de Grau — ETSETB, Universitat Politècnica de Catalunya (UPC).
*Disseny i implementació d'un sistema federat de Network Digital Twins (NDT) amb
suport per a funcions de realitat estesa (XRF).*
