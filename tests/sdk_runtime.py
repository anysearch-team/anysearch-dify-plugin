"""SDK stdio 的合成 HTTP fixture；不会打入插件包。"""
import dify_plugin  # noqa: F401
import json
from pathlib import Path
import runpy
import sys

import httpx

sys.path.insert(0, str(Path.cwd()))


def synthetic(self, request):
    if request.method == "GET":
        row = {"domain": "code", "description": "Code", "sub_domain_count": 1}
        if request.url.path == "/v1/sub-domains":
            row["sub_domains"] = [{"sub_domain":"code.doc", "description":"Code docs", "params":{
                "library":{"description":"Library or framework name to query docs for, e.g. react, express", "required":True}}}]
        return httpx.Response(200, json={"code": 0, "request_id": "synthetic-sdk-04", "data": {
            "domains": [row]}})
    body = json.loads(request.content)
    if request.url.path == '/v1/extract':
        return httpx.Response(200, json={'code': 0, 'request_id': 'synthetic-sdk-07', 'data': {'content': 'Extracted content'}})
    if body["query"] == "synthetic-business-failure":
        return httpx.Response(200, json={"code": 9, "message": "private-body-sentinel", "auto_registered": {"api_key": "returned-secret-sentinel"}})
    return httpx.Response(200, json={"code": 0, "request_id": "synthetic-sdk-03", "data": {"results": []}})


httpx.HTTPTransport.handle_request = synthetic
runpy.run_module("main", run_name="__main__")
