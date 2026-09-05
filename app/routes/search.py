"""Global command-palette search blueprint.

Backs the Ctrl+K overlay (JSON) and a dedicated results page (HTML). Results
are permission-scoped by the search service, so a user never sees identifiers
for patients they have no documented relationship with.
"""
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from app.routes.decorators import permissions_required
from app.services.search import global_search
from app.permissions import SEARCH_GLOBAL

search_bp = Blueprint('search', __name__)


def _result_links(group, item):
    """Map a search result to the best cross-department destination."""
    kind = group
    patient_url = f'/clinical/patient/{item.get("patient_id", 0)}'
    anchors = {
        'patients': ('/clinical/patient/{id}', 'Patient'),
        'doctors': ('/clinical', 'Staff'),
        'appointments': ('/clinical/patient/{pid}', 'Appointment'),
        'lab_orders': ('/clinical/patient/{pid}', 'Lab Order'),
        'radiology_orders': ('/clinical/patient/{pid}', 'Imaging'),
        'prescriptions': ('/clinical/patient/{pid}', 'Prescription'),
        'referrals': ('/clinical/patient/{pid}', 'Referral'),
        'documents': ('/clinical/patient/{pid}', 'Document'),
        'tasks': ('/clinical/patient/{pid}', 'Task'),
    }
    url_tpl, kind_label = anchors.get(kind, (patient_url, kind))
    if '{pid}' in url_tpl:
        url = url_tpl.format(pid=item.get('patient_id') or item['id'])
    else:
        url = url_tpl.format(id=item['id'])
    return url, kind_label


@search_bp.route('')
@login_required
@permissions_required(SEARCH_GLOBAL)
def search():
    q = request.args.get('q', '')
    results = global_search(q)
    is_json = (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
               or request.args.get('type') == 'json')
    if is_json:
        # Enrich with patient_id + destination links for the palette JS.
        payload = {}
        for kind, items in results.items():
            out = []
            for item in items:
                row = dict(item)
                pid = row.pop('patient_id', None)
                row['patient_id'] = pid or row.get('id')
                row['url'], row['kind'] = _result_links(kind, row)
                out.append(row)
            payload[kind] = out
        return jsonify({'query': q, 'groups': payload})
    return render_template('search/results.html', title='Search',
                           query=q, results=results)