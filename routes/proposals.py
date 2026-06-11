"""
routes/proposals.py — Bidirectional sync: Twin → Original proposal system.

FLOW
────
  Twin (JS) clicks "Propose to Original"
      → POST /propose_to_original  (Twin's own server)
          → POST /propose  (Original's server)
              → queued in pending_proposals
                  → Original dashboard shows it
                      → user clicks Approve / Reject
                          Approve → apply via /add_host or /add_router + sync to all Twins
                          Reject  → discard, done

TWIN STATUS
───────────
  Each Twin can be in one of three states:
    connected    → receives all sync changes normally
    offline      → no heartbeat received recently (network/process down)
    disconnected → actively disconnected by the Original, either manually or
                   automatically (sync failure, or local modification detected
                   via topology hash mismatch in the heartbeat)

  Reconnecting a disconnected Twin ALWAYS performs a full resync first —
  a Twin can never rejoin without restoring the Original's state.

PROPOSAL HISTORY
────────────────
  All proposals are kept in memory with their final status (approved /
  rejected) and an optional comment written by the Original's operator.
  The Original sees the full history; each Twin only sees its own.

Endpoints on Original:
  GET  /proposals                   → list pending proposals
  GET  /proposals/history           → full history (optional ?twin_ip= filter)
  POST /propose                     → receive proposal from a Twin
  POST /proposals/approve/<id>      → approve and apply (optional comment)
  POST /proposals/reject/<id>       → reject and discard (optional comment)
  GET  /proposals/twin_status       → Twin states + reasons
  POST /proposals/twin_action       → disconnect / reconnect (resync+connect)

Endpoint on Twin:
  POST /propose_to_original         → forward proposal to Original
  GET  /proposals/my_history        → own proposal history (from Original)
  GET  /twin/my_status              → own status as seen by the Original
"""

import threading
import time
import uuid

import requests as _req
from flask import Blueprint, jsonify, request

bp = Blueprint('proposals', __name__)

# Injected by app.py
_xarxa   = None
_IS_TWIN = False


def init_blueprint(xarxa_instance, is_twin):
    global _xarxa, _IS_TWIN
    _xarxa   = xarxa_instance
    _IS_TWIN = is_twin


# ═══════════════════════════════════════════════════════════════════════
# PENDING PROPOSALS QUEUE
# ═══════════════════════════════════════════════════════════════════════
# FIFO queue of proposals sent by Twins waiting for Original approval.
# Each entry: {id, twin_ip, op_type, payload, timestamp, status}

_pending   = {}          # {id: proposal_dict}  — ordered by insertion
_pend_lock = threading.Lock()


def _new_proposal(twin_ip, op_type, payload):
    pid = str(uuid.uuid4())[:8]
    return {
        'id':          pid,
        'twin_ip':     twin_ip,
        'op_type':     op_type,   # 'add_host' | 'add_router' | 'remove_node'
        'payload':     payload,
        'timestamp':   round(time.time(), 2),
        'status':      'pending',
        'comment':     None,      # comentari de l'Original en aprovar/rebutjar
        'resolved_at': None,      # timestamp de la resolució
    }


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINTS — ORIGINAL SIDE
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/proposals')
def list_proposals():
    """Return all proposals (pending, approved, rejected) for the dashboard."""
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})
    with _pend_lock:
        proposals = list(_pending.values())
    # Sort: pending first, then by timestamp
    proposals.sort(key=lambda p: (0 if p['status'] == 'pending' else 1, p['timestamp']))
    return jsonify({'ok': True, 'proposals': proposals})


@bp.route('/proposals/history')
def proposals_history():
    """
    Full proposal history (Original side), most recent first.
    Optional ?twin_ip=X.X.X.X filter — used by Twins to retrieve ONLY their
    own proposals (a Twin must never see other Twins' history).
    """
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})
    twin_ip = request.args.get('twin_ip', '').strip()
    with _pend_lock:
        proposals = list(_pending.values())
    if twin_ip:
        proposals = [p for p in proposals if p['twin_ip'] == twin_ip]
    proposals.sort(key=lambda p: p['timestamp'], reverse=True)
    return jsonify({'ok': True, 'history': proposals})


@bp.route('/propose', methods=['POST'])
def receive_proposal():
    """
    Receive a proposal from a Twin.
    The proposal is queued — NOT applied immediately.
    Returns the proposal_id so the Twin can track its status.
    """
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})

    data    = request.json or {}
    op_type = data.get('op_type')
    payload = data.get('payload', {})

    # Accept the same Twin IP from X-Forwarded-For or remote_addr
    twin_ip = (request.headers.get('X-Twin-IP')
               or request.remote_addr
               or 'unknown')

    if op_type not in ('add_host', 'add_router', 'remove_node'):
        return jsonify({'ok': False, 'error': f'Unknown op_type: {op_type}'})

    proposal = _new_proposal(twin_ip, op_type, payload)
    with _pend_lock:
        _pending[proposal['id']] = proposal

    # Update Twin last-seen in sync module
    from sync import _touch_twin
    _touch_twin(twin_ip)

    print(f'[proposals] Received {op_type} from {twin_ip} '
          f'(id={proposal["id"]}, name={payload.get("name","?")})')
    return jsonify({'ok': True, 'proposal_id': proposal['id']})


@bp.route('/proposals/approve/<proposal_id>', methods=['POST'])
def approve_proposal(proposal_id):
    """
    Approve a proposal: apply it to the Original and sync to all Twins.
    Uses the same /add_host / /add_router / /remove_node endpoints
    (which handle sync to Twins automatically).
    """
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})

    # Comprovació + transició d'estat ATÒMIQUES dins el lock: abans, dos
    # clics simultanis a Approve passaven tots dos el check 'pending' i
    # l'operació s'aplicava DUES vegades. 'applying' actua de guarda.
    with _pend_lock:
        proposal = _pending.get(proposal_id)
        if not proposal:
            return jsonify({'ok': False, 'error': 'Proposal not found'})
        if proposal['status'] != 'pending':
            return jsonify({'ok': False, 'error': f'Proposal already {proposal["status"]}'})
        proposal['status'] = 'applying'

    op_type = proposal['op_type']
    payload = proposal['payload']
    comment = (request.json or {}).get('comment') or None

    # Apply the operation by calling the existing endpoint on localhost.
    # Flask threaded=True handles concurrent requests so the loopback call
    # is processed in a separate thread — no deadlock.
    try:
        r = _req.post(f'http://localhost:5000/{op_type}',
                      json=payload, timeout=35)
        resp = r.json()
    except Exception as e:
        with _pend_lock:
            proposal['status'] = 'pending'   # permet reintentar
        return jsonify({'ok': False, 'error': f'Apply failed: {e}'})

    if resp.get('ok'):
        with _pend_lock:
            proposal['status']      = 'approved'
            proposal['comment']     = comment
            proposal['resolved_at'] = round(time.time(), 2)
        print(f'[proposals] Approved {op_type} id={proposal_id}')
        return jsonify({'ok': True})
    else:
        err = resp.get('error', 'Unknown error')
        with _pend_lock:
            proposal['status'] = 'pending'   # permet reintentar
        return jsonify({'ok': False, 'error': err})


@bp.route('/proposals/reject/<proposal_id>', methods=['POST'])
def reject_proposal(proposal_id):
    """Reject a proposal — mark as rejected, do not apply. Optional comment."""
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})

    comment = (request.json or {}).get('comment') or None
    with _pend_lock:
        proposal = _pending.get(proposal_id)
        if not proposal:
            return jsonify({'ok': False, 'error': 'Proposal not found'})
        if proposal['status'] != 'pending':
            return jsonify({'ok': False, 'error': f'Proposal already {proposal["status"]}'})
        proposal['status']      = 'rejected'
        proposal['comment']     = comment
        proposal['resolved_at'] = round(time.time(), 2)

    print(f'[proposals] Rejected {proposal["op_type"]} id={proposal_id}')
    return jsonify({'ok': True})


@bp.route('/proposals/twin_status')
def get_twin_status():
    """Return status + reason for each known Twin."""
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})
    from sync import get_twin_statuses
    return jsonify({'ok': True, 'twin_status': get_twin_statuses()})


@bp.route('/proposals/twin_action', methods=['POST'])
def twin_action():
    """
    Perform a manual action on a Twin:
      action='disconnect'  → stop sending changes to this Twin
      action='reconnect'   → full resync + re-enable sync (always together:
                             a Twin can never rejoin without restoring the
                             Original's state first)
    """
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})

    data    = request.json or {}
    twin_ip = data.get('twin_ip')
    action  = data.get('action')

    if not twin_ip:
        return jsonify({'ok': False, 'error': 'twin_ip required'})

    from sync import set_twin_status, get_twin_statuses, resync_one_twin, TWINS

    if action == 'disconnect':
        set_twin_status(twin_ip, 'disconnected',
                        reason='disconnected manually by Original')
        return jsonify({'ok': True, 'action': 'disconnected'})

    elif action == 'reconnect':
        twin = next((t for t in TWINS if t['ip'] == twin_ip), None)
        if not twin:
            return jsonify({'ok': False, 'error': 'Twin IP not in TWINS list'})
        # Resync síncron: la reconnexió només es completa si el snapshot
        # s'aplica correctament. resync_one_twin posa status='connected'
        # (i neteja el reason) si té èxit.
        resync_one_twin(_xarxa, twin)
        status = get_twin_statuses().get(twin_ip, {}).get('status')
        if status == 'connected':
            return jsonify({'ok': True, 'action': 'reconnected'})
        return jsonify({'ok': False,
                        'error': 'Resync failed — Twin remains disconnected'})

    return jsonify({'ok': False, 'error': f'Unknown action: {action}'})


@bp.route('/proposals/status/<proposal_id>')
def proposal_status(proposal_id):
    """Check the status of a specific proposal (for Twin polling)."""
    with _pend_lock:
        proposal = _pending.get(proposal_id)
    if not proposal:
        return jsonify({'ok': False, 'error': 'Not found'})
    return jsonify({'ok': True, 'status': proposal['status']})


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINTS — REGISTRATION & HEARTBEAT (called by Twins, received by Original)
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/twin/register', methods=['POST'])
def twin_register():
    """
    Called by a Twin when it starts.
    Works even if the Twin IP was not in the Original's --twins list.
    """
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})
    data      = request.json or {}
    twin_ip   = data.get('ip') or request.remote_addr
    twin_port = int(data.get('port', 5000))
    from sync import register_twin
    register_twin(twin_ip, twin_port)
    print(f'[proposals] Twin registered: {twin_ip}:{twin_port}')
    return jsonify({'ok': True})


@bp.route('/twin/heartbeat', methods=['POST'])
def twin_heartbeat():
    """
    Periodic heartbeat (every 3s). Updates last_seen, restores 'offline' → 'connected'.

    El heartbeat porta el hash de la topologia del Twin: si difereix del de
    l'Original durant 2 heartbeats consecutius, el Twin es desconnecta
    automàticament (modificació local detectada). La resposta retorna
    l'estat del Twin perquè aquest sàpiga si ha estat desconnectat i per què.
    """
    if _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Original'})
    data      = request.json or {}
    twin_ip   = data.get('ip') or request.remote_addr
    twin_port = int(data.get('port', 5000))
    from sync import register_twin, check_twin_hash, get_twin_statuses
    register_twin(twin_ip, twin_port)
    check_twin_hash(twin_ip, data.get('topo_hash'))

    st = get_twin_statuses().get(twin_ip, {})
    return jsonify({'ok': True,
                    'status': st.get('status', 'connected'),
                    'reason': st.get('reason')})


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT — TWIN SIDE
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/propose_to_original', methods=['POST'])
def propose_to_original():
    """
    Called by the Twin's own JS to forward a proposal to the Original.
    The Twin doesn't need to know the Original's IP — it sends here and
    this endpoint forwards to the Original.
    """
    if not _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Twin'})

    from sync import ORIGINAL_IP
    data = request.json or {}

    # Add the Twin's own IP so the Original can identify the sender
    try:
        r = _req.post(
            f'http://{ORIGINAL_IP}:5000/propose',
            json=data,
            headers={'X-Twin-IP': _get_own_ip()},
            timeout=10,
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Could not reach Original: {e}'})


@bp.route('/proposals/my_status/<proposal_id>')
def my_proposal_status(proposal_id):
    """
    Twin polls this to check if a proposal it submitted was approved/rejected.
    Forwards the request to the Original.
    """
    if not _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Twin'})

    from sync import ORIGINAL_IP
    try:
        r = _req.get(
            f'http://{ORIGINAL_IP}:5000/proposals/status/{proposal_id}',
            timeout=5,
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@bp.route('/proposals/my_history')
def my_proposal_history():
    """
    Twin-side: retrieve the history of proposals submitted by THIS Twin.
    Forwards to the Original with the Twin's own IP as filter, so a Twin
    can never see other Twins' proposals.
    """
    if not _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Twin'})

    from sync import ORIGINAL_IP
    try:
        r = _req.get(
            f'http://{ORIGINAL_IP}:5000/proposals/history',
            params={'twin_ip': _get_own_ip()},
            timeout=5,
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@bp.route('/twin/my_status')
def twin_my_status():
    """
    Twin-side: own status as seen by the Original (updated on every
    heartbeat response). Lets the Twin dashboard show that it has been
    disconnected and why.
    """
    if not _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Twin'})
    from sync import TWIN_SELF_STATUS
    return jsonify({'ok': True, **TWIN_SELF_STATUS})


# ── Helper ──

def _get_own_ip():
    """Best-effort: get our own outbound IP. Delega a sync._get_own_ip, que
    funciona també en laboratoris aïllats sense ruta a Internet."""
    from sync import _get_own_ip as _sync_get_own_ip
    return _sync_get_own_ip()