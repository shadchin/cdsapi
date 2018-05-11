import requests
import json
import time
import datetime
import os


def bytes_to_string(n):
    u = ['', 'K', 'M', 'G', 'T', 'P']
    i = 0
    while n >= 1024:
        n /= 1024.0
        i += 1
    return "%g%s" % (int(n * 10 + 0.5) / 10.0, u[i])


def robust(call):

    def wrapped(*args, **kwargs):
        return call(*args, **kwargs)

    return wrapped


class Client(object):

    def __init__(self,
                 end_point=os.environ.get("CDSAPI_URL"),
                 api_key=os.environ.get("CDSAPI_KEY"),
                 verbose=False,
                 verify=None,
                 timeout=None, full_stack=False):

        dotrc = os.environ.get("CDSAPI_RC", os.path.expanduser('~/.cdsapirc'))

        if end_point is None or api_key is None:
            if os.path.exists(dotrc):
                config = {}
                with open(dotrc) as f:
                    for l in f.readlines():
                        k, v = l.strip().split(':', 1)
                        config[k] = v.strip()
                url = config.get('url')
                key = config.get('key')

                if api_key is None:
                    api_key = key

                if end_point is None:
                    end_point = url

                if verify is None:
                    verify = int(config.get('verify', 1))

        if end_point is None or api_key is None or api_key is None:
            raise Exception("Missing/incomplete configuration file: %s" % (dotrc))

        self.end_point = end_point
        self.api_key = api_key

        self.verbose = verbose
        self.verify = True if verify else False
        self.timeout = timeout
        self.sleep_max = 120
        self.full_stack = full_stack

        self._trace(dict(end_point=self.end_point,
                         api_key=self.api_key,
                         verbose=self.verbose,
                         verify=self.verify,
                         timeout=self.timeout,
                         sleep_max=self.sleep_max,
                         full_stack=self.full_stack,
                         ))

    def get_resource(self, name, request, target=None):
        self._api("%s/resources/%s" % (self.end_point, name), request, target)

    def _download(self, url, size, local_filename=None):

        if local_filename is None:
            local_filename = url.split('/')[-1]

        r = robust(requests.get)(url, stream=True, verify=self.verify)
        total = 0
        with open(local_filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)

        assert total == size
        return local_filename

    def _api(self, url, request, target):

        session = requests.Session()
        session.auth = tuple(self.api_key.split(':', 2))

        self._trace("POST %s %s" % (url, json.dumps(request)))
        result = robust(session.post)(url, json=request, verify=self.verify)
        reply = {}
        print(result)

        try:
            reply = result.json()
            result.raise_for_status()
            reply = result.json()
        except Exception:
            if 'message' in reply:
                error = reply['message']

                if 'context' in reply and 'required_terms' in reply['context']:
                    e = [error]
                    for t in reply['context']['required_terms']:
                        e.append("To access this resource, you first need to accept the terms of '%s' at %s" %
                                 (t['title'], t['url']))
                    error = '. '.join(e)
                raise Exception(error)
            else:
                raise

        sleep = 1
        start = time.time()

        while True:

            self._trace(reply)

            if reply['state'] == 'completed':

                if target:
                    self._download(reply['location'], int(reply['content_length']), target)
                else:
                    self._trace("HEAD %s" % (reply['location'],))
                    metadata = robust(session.head)(reply['location'], verify=self.verify)
                    metadata.raise_for_status()
                    self._trace(metadata.headers)

                if 'request_id' in reply:
                    rid = reply['request_id']

                    task_url = "%s/tasks/%s" % (self.end_point, rid)
                    self._trace("DELETE %s" % (task_url,))
                    delete = session.delete(task_url, verify=self.verify)
                    self._trace("DELETE returns %s %s" % (delete.status_code, delete.reason))
                    try:
                        delete.raise_for_status()
                    except Exception:
                        self._warning("DELETE %s returns %s %s" % (task_url, delete.status_code, delete.reason))

                self._trace("Done")
                return

            if reply['state'] in ('queued', 'running'):
                rid = reply['request_id']

                if self.timeout and (time.time() - start > self.timeout):
                    raise Exception("TIMEOUT")

                self._trace("Request ID is %s, sleep %s" % (rid, sleep))
                time.sleep(sleep)
                sleep *= 1.5
                if sleep > self.sleep_max:
                    sleep = self.sleep_max

                task_url = "%s/tasks/%s" % (self.end_point, rid)
                self._trace("GET %s" % (task_url,))

                result = robust(session.get)(task_url, verify=self.verify)
                result.raise_for_status()
                reply = result.json()
                continue

            if reply['state'] in ('failed',):
                print("Message: %s" % (reply['error'].get("message"),))
                print("Reason:  %s" % (reply['error'].get("reason"),))
                for n in reply.get('error', {}).get('context', {}).get('traceback', '').split('\n'):
                    if n.strip() == '' and not self.full_stack:
                        break
                    print("  %s" % (n,))
                raise Exception(reply['error'].get("reason"),)

            raise Exception("Unknown API state [%s]" % (reply['state'],))

    def _trace(self, what):
        if isinstance(what, (dict, list)):
            what = json.dumps(what, indent=4, sort_keys=True)

        ts = "{:%Y-%m-%d %H:%M:%S}".format(datetime.datetime.now())
        print('CDS-API %s %s' % (ts, what))
