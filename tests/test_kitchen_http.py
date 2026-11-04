"""Kitchen endpoints through the real app: auth gates, share links, ICS, Sous Chef errors."""

import os
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'ui'))

from app_harness import result as _result, run as _run  # noqa: E402

PASSWORD = 'a properly long passphrase'

_SCRIPT = f"""
    import database, passwords
    from app import app
    app.config['TESTING'] = True

    def sign_in(username):
        client = app.test_client()
        resp = client.post('/auth/local/login', json={{'username': username, 'password': {PASSWORD!r}}})
        assert resp.status_code == 200, resp.data
        return client

    database.claim_first_local_admin('boss', passwords.hash_password({PASSWORD!r}))
    database.create_local_user('cook', passwords.hash_password({PASSWORD!r}), is_admin=False)
    boss, cook = sign_in('boss'), sign_in('cook')
    anon = app.test_client()

    out = {{}}
    out['anon_list'] = anon.get('/api/kitchen/recipes').status_code
    out['anon_plan'] = anon.get('/api/kitchen/plan?start=2026-01-01&end=2026-01-07').status_code

    created = cook.post('/api/kitchen/recipes', json={{
        'name': 'Lentil soup', 'servings': 4,
        'ingredients': ['200 g lentils', '1 onion', '1 l water'],
        'steps': ['Simmer 25 minutes.'], 'tags': ['Soup'],
    }})
    out['create'] = created.status_code
    rid = created.get_json()['id']

    got = boss.get(f'/api/kitchen/recipes/{{rid}}?servings=2').get_json()
    out['lentils'] = next(i['amount'] for i in got['ingredients'] if i['food'] == 'lentils')
    out['listed'] = boss.get('/api/kitchen/recipes?q=lentil').get_json()['total']

    boss.put(f'/api/kitchen/recipes/{{rid}}/me', json={{'favorite': True}})
    out['cook_fav'] = cook.get(f'/api/kitchen/recipes/{{rid}}').get_json()['user']['favorite']

    share = cook.post(f'/api/kitchen/recipes/{{rid}}/share').get_json()['url']
    public = anon.get('/api/public/share/' + share.rsplit('/', 1)[1])
    out['share_status'] = public.status_code
    out['share_keys'] = sorted(public.get_json())
    out['share_cache'] = public.headers.get('Cache-Control')
    cook.delete(f'/api/kitchen/recipes/{{rid}}/share')
    out['share_revoked'] = anon.get('/api/public/share/' + share.rsplit('/', 1)[1]).status_code

    cook.post('/api/kitchen/plan', json={{'date': '2026-11-03', 'slot': 'dinner', 'recipe_id': rid}})
    ics_url = cook.post('/api/kitchen/plan/ics').get_json()['url']
    ics = anon.get('/ics/' + ics_url.rsplit('/ics/', 1)[1])
    out['ics_status'] = ics.status_code
    out['ics_type'] = ics.headers.get('Content-Type')
    out['ics_bad'] = anon.get('/ics/not-a-token.ics').status_code

    out['suggest_no_ai'] = cook.post('/api/kitchen/souschef/suggest', json={{'ingredients': ['lentils'], 'ai': False}}).get_json()['picks'][0]['recipe']['id'] == rid
    out['invent_status'] = cook.post('/api/kitchen/souschef/invent', json={{'ingredients': ['lentils']}}).status_code

    out['remote_as_cook'] = cook.get('/api/kitchen/import/remote/mealie').status_code
    out['bad_json'] = cook.post('/api/kitchen/recipes', data='[1]', content_type='application/json').status_code
    out['cook_delete'] = cook.delete(f'/api/kitchen/recipes/{{rid}}').status_code
    emit(**out)
"""


class TestKitchenHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _result(_run(textwrap.dedent(_SCRIPT), AUTH_MODE='local', KITCHEN_BACKFILL='false'))

    def test_anonymous_is_rejected(self):
        self.assertEqual(self.res['anon_list'], 401)
        self.assertEqual(self.res['anon_plan'], 401)

    def test_shared_library_with_scaling(self):
        self.assertEqual(self.res['create'], 201)
        self.assertEqual(self.res['lentils'], 100)
        self.assertEqual(self.res['listed'], 1)

    def test_favorites_are_per_user(self):
        self.assertFalse(self.res['cook_fav'])

    def test_share_link_is_public_until_revoked(self):
        self.assertEqual(self.res['share_status'], 200)
        self.assertNotIn('user', self.res['share_keys'])
        self.assertNotIn('created_by', self.res['share_keys'])
        self.assertNotIn('share_links', self.res['share_keys'])
        self.assertIn('no-store', self.res['share_cache'])
        self.assertEqual(self.res['share_revoked'], 404)

    def test_ics_feed(self):
        self.assertEqual(self.res['ics_status'], 200)
        self.assertTrue(self.res['ics_type'].startswith('text/calendar'))
        self.assertEqual(self.res['ics_bad'], 404)

    def test_souschef(self):
        self.assertTrue(self.res['suggest_no_ai'])
        # The test env has a provider with no key, so the AI call fails cleanly.
        self.assertEqual(self.res['invent_status'], 503)

    def test_admin_only_and_validation(self):
        self.assertEqual(self.res['remote_as_cook'], 403)
        self.assertEqual(self.res['bad_json'], 400)
        self.assertEqual(self.res['cook_delete'], 200)


if __name__ == '__main__':
    unittest.main()
