"""动态目录矩阵的合成协议验收。

矩阵由 2026-10-10 的实时目录对照记录生成；这里验证插件的通用路径，
不把保存的快照当作当前上游可用性证明。
"""

import json
from pathlib import Path

import httpx

from anysearch.catalog import Catalog
from anysearch.client import AnySearchClient


MATRIX = json.loads(
    (Path(__file__).parent / "fixtures" / "vertical_catalog_matrix.json").read_text(
        encoding="utf-8"
    )
)


class MatrixService:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            tag = body["tag"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "request_id": f"search-{tag}",
                    "data": {
                        "results": [
                            {
                                "title": f"Protocol result for {tag}",
                                "url": f"https://example.test/{tag}",
                                "content": "synthetic protocol evidence",
                            }
                        ]
                    },
                },
            )

        if request.url.path == "/v1/domains":
            domains = [
                {
                    "domain": domain,
                    "description": "Synthetic directory fixture",
                    "sub_domain_count": sum(
                        row["domain"] == domain for row in MATRIX
                    ),
                }
                for domain in sorted({row["domain"] for row in MATRIX})
            ]
        else:
            domain = request.url.params["domain"]
            domains = [
                {
                    "domain": domain,
                    "description": "Synthetic directory fixture",
                    "sub_domain_count": sum(
                        row["domain"] == domain for row in MATRIX
                    ),
                    "sub_domains": [
                        {
                            "sub_domain": row["tag"],
                            "description": "Synthetic capability",
                            "params": {
                                parameter["name"]: {
                                    "description": "Synthetic parameter",
                                    "required": parameter["required"],
                                }
                                for parameter in row["parameters"]
                            },
                        }
                        for row in MATRIX
                        if row["domain"] == domain
                    ],
                }
            ]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "request_id": "catalog",
                "data": {
                    "topology_version": 9,
                    "topology_generation": "matrix",
                    "domains": domains,
                },
            },
        )


def test_all_dynamic_tags_complete_catalog_to_protocol_path() -> None:
    assert len(MATRIX) == 40
    assert len({row["tag"] for row in MATRIX}) == 40

    service = MatrixService()
    transport = httpx.MockTransport(service)

    for row in MATRIX:
        params = {
            parameter["name"]: "fixture-value"
            for parameter in row["parameters"]
            if parameter["required"]
        }
        result = AnySearchClient({}, transport=transport).vertical_search(
            {
                "query": "protocol acceptance",
                "tag": row["tag"],
                "params_json": json.dumps(params),
                "max_results": 2,
            },
            catalog=Catalog(transport=transport),
        )

        assert result["status"] == "ok"
        assert result["tag"] == row["tag"]
        assert result["result_count"] == 1
        assert result["catalog"]["topology_version"] == "9"
        request = json.loads(service.calls[-1].content)
        assert request["tag"] == row["tag"]
        assert request["max_results"] == 2
        if params:
            assert request["params"] == params
        else:
            assert "params" not in request

    assert len([request for request in service.calls if request.method == "POST"]) == 40
