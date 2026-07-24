"""lecture段階成果物の固定検証テスト。"""

import json

from scripts.validate_lecture_stage import (
    _outline_errors,
    _safety_errors,
    _script_provenance_errors,
    _understanding_errors,
    validate_stage,
)


def test_completed_research_requires_related_material() -> None:
    understanding = {
        "major_claims": [{"id": "C1"}],
        "research": {
            "status": "completed",
            "related_materials": [],
            "notes": [],
        },
    }

    errors = _understanding_errors(understanding)

    assert any("related_materialsを1件以上" in error for error in errors)


def test_community_context_requires_reception_role_and_caveat() -> None:
    understanding = {
        "major_claims": [{"id": "C1"}],
        "research": {
            "status": "completed",
            "search_queries": ["技術名 公式", "技術名 Hacker News"],
            "related_materials": [
                {
                    "id": "R1",
                    "material_type": "community_discussion",
                    "evidential_role": "corroboration",
                    "caveat": "",
                }
            ],
            "notes": [],
        },
    }

    errors = _understanding_errors(understanding)

    assert any("evidential_role=reception" in error for error in errors)
    assert any("代表性の限界をcaveat" in error for error in errors)


def test_community_context_rejects_positive_representativeness_claim() -> None:
    value = {
        "major_claims": [],
        "research": {
            "status": "completed",
            "search_queries": ["query one", "query two"],
            "related_materials": [
                {
                    "id": "R1",
                    "material_type": "community_discussion",
                    "evidential_role": "reception",
                    "caveat": "利用者全体を代表します",
                }
            ],
        },
    }

    errors = _understanding_errors(value)

    assert any("caveatで代表性を限定" in error for error in errors)


def test_community_context_rejects_double_negative_caveat() -> None:
    value = {
        "major_claims": [],
        "research": {
            "status": "completed",
            "search_queries": ["query one", "query two"],
            "related_materials": [
                {
                    "id": "R1",
                    "material_type": "community_discussion",
                    "evidential_role": "reception",
                    "caveat": "利用者全体を代表しないわけではありません",
                }
            ],
        },
    }

    errors = _understanding_errors(value)

    assert any("caveatで代表性を限定" in error for error in errors)


def test_community_context_rejects_negation_reversal_caveat() -> None:
    value = {
        "major_claims": [],
        "research": {
            "status": "completed",
            "search_queries": ["query one", "query two"],
            "related_materials": [
                {
                    "id": "R1",
                    "material_type": "community_discussion",
                    "evidential_role": "reception",
                    "caveat": "利用者全体を代表しない、という主張は誤りです",
                }
            ],
        },
    }

    errors = _understanding_errors(value)

    assert any("caveatで代表性を限定" in error for error in errors)


def test_outline_rejects_unknown_context_id(tmp_path) -> None:
    understanding = {
        "major_claims": [{"id": "C1"}],
        "research": {
            "status": "completed",
            "related_materials": [{"id": "R1"}],
            "notes": [],
        },
    }
    (tmp_path / "source-understanding.json").write_text(
        json.dumps(understanding, ensure_ascii=False),
        encoding="utf-8",
    )
    outline = {
        "scenes": [
            {
                "scene_number": 1,
                "claim_ids": ["C1"],
                "context_ids": ["R2"],
            }
        ]
    }

    errors = _outline_errors(tmp_path, outline)

    assert errors == [
        "teaching-outline.json scene 1: 未知のcontext_ids ['R2']"
    ]


def test_outline_rejects_duplicate_provenance_ids(tmp_path) -> None:
    understanding = {
        "major_claims": [{"id": "C1"}],
        "research": {
            "related_materials": [{"id": "R1"}],
        },
    }
    (tmp_path / "source-understanding.json").write_text(
        json.dumps(understanding, ensure_ascii=False),
        encoding="utf-8",
    )

    errors = _outline_errors(
        tmp_path,
        {
            "scenes": [
                {
                    "scene_number": 1,
                    "claim_ids": ["C1", "C1"],
                    "context_ids": ["R1", "R1"],
                }
            ]
        },
    )

    assert errors == [
        "teaching-outline.json scene 1: claim_idsが重複",
        "teaching-outline.json scene 1: context_idsが重複",
    ]


def test_script_preserves_outline_provenance_ids(tmp_path) -> None:
    understanding = {
        "major_claims": [{"id": "C1"}, {"id": "C2"}],
        "research": {"related_materials": []},
    }
    outline = {
        "scenes": [
            {
                "scene_number": 1,
                "claim_ids": ["C1", "C2"],
                "context_ids": [],
            }
        ]
    }
    (tmp_path / "source-understanding.json").write_text(
        json.dumps(understanding, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "teaching-outline.json").write_text(
        json.dumps(outline, ensure_ascii=False),
        encoding="utf-8",
    )

    errors = _script_provenance_errors(
        tmp_path,
        {
            "scenes": [
                {
                    "claim_ids": ["C2", "C1"],
                    "context_ids": [],
                    "context_disclosures": [],
                    "lines": [],
                }
            ]
        },
    )

    assert errors == ["scene 1: claim_idsを教える順番から変えない"]


def test_script_requires_spoken_community_disclosure(tmp_path) -> None:
    understanding = {
        "major_claims": [{"id": "C1"}],
        "research": {
            "related_materials": [
                {
                    "id": "R1",
                    "material_type": "community_discussion",
                }
            ],
        },
    }
    outline = {
        "scenes": [
            {
                "scene_number": 1,
                "claim_ids": ["C1"],
                "context_ids": ["R1"],
            }
        ]
    }
    (tmp_path / "source-understanding.json").write_text(
        json.dumps(understanding, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "teaching-outline.json").write_text(
        json.dumps(outline, ensure_ascii=False),
        encoding="utf-8",
    )
    script = {
        "scenes": [
            {
                "claim_ids": ["C1"],
                "context_ids": ["R1"],
                "context_disclosures": [
                    {
                        "context_id": "R1",
                        "material_type": "community_discussion",
                        "source_text": "Hacker Newsの投稿",
                        "limitation_text": "利用者全体を代表しません",
                    }
                ],
                "lines": [{"text": "Hacker Newsの投稿では意見が割れました。"}],
            }
        ]
    }

    errors = _script_provenance_errors(tmp_path, script)

    assert errors == ["scene 1: R1の代表性の限界を実際に発話する"]


def test_script_rejects_positive_representativeness_disclosure(tmp_path) -> None:
    understanding = {
        "major_claims": [{"id": "C1"}],
        "research": {
            "related_materials": [
                {
                    "id": "R1",
                    "material_type": "community_discussion",
                }
            ],
        },
    }
    outline = {
        "scenes": [
            {
                "scene_number": 1,
                "claim_ids": ["C1"],
                "context_ids": ["R1"],
            }
        ]
    }
    (tmp_path / "source-understanding.json").write_text(
        json.dumps(understanding, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "teaching-outline.json").write_text(
        json.dumps(outline, ensure_ascii=False),
        encoding="utf-8",
    )
    limitation = "利用者全体を代表します"
    script = {
        "scenes": [
            {
                "claim_ids": ["C1"],
                "context_ids": ["R1"],
                "context_disclosures": [
                    {
                        "context_id": "R1",
                        "material_type": "community_discussion",
                        "source_text": "Hacker Newsの投稿",
                        "limitation_text": limitation,
                    }
                ],
                "lines": [
                    {
                        "text": (
                            "Hacker Newsの投稿では意見が割れ、"
                            f"{limitation}。"
                        )
                    }
                ],
            }
        ]
    }

    errors = _script_provenance_errors(tmp_path, script)

    assert errors == [
        "scene 1: R1のlimitation_textで代表性の限界を明示する"
    ]

    valid_limitation = "利用者全体を代表しません"
    script["scenes"][0]["context_disclosures"][0]["limitation_text"] = (
        valid_limitation
    )
    script["scenes"][0]["lines"][0]["text"] += f"{valid_limitation}。"
    assert _script_provenance_errors(tmp_path, script) == []


def test_safety_rejects_non_http_links_and_bare_secret_tokens() -> None:
    token = "ghp_" + ("x" * 24)
    anthropic_token = "sk-ant-api03-" + ("x" * 24)
    openai_token = "sk-proj-" + ("x" * 24)
    legacy_openai_token = "sk-" + ("x" * 48)
    private_key = (
        "-----BEGIN "
        "PRIVATE KEY-----\nsecret-body\n-----END PRIVATE KEY-----"
    )
    value = {
        "domain": "example.com",
        "ip": "192.168.1.5/private?token=secret",
        "localhost": "localhost/private?token=secret",
        "bare_link": "example.com/private?token=secret",
        "uppercase_localhost": "LOCALHOST/private?token=secret",
        "uppercase_bare_link": "EXAMPLE.COM/private?token=secret",
        "bare_link_with_port": "EXAMPLE.COM:8443/private?token=secret",
        "less_common_domains": [
            "example.us",
            "example.shop",
            "example.museum",
        ],
        "ftp_link": "ftp://private.example/secret",
        "data_uri": "data:text/plain,secret",
        "urn": "urn:example:secret",
        "client_secret": "client_secret=secret",
        "tokens": [
            token,
            anthropic_token,
            openai_token,
            legacy_openai_token,
            private_key,
        ],
    }

    errors = _safety_errors(value, "source-understanding.json")

    assert errors == [
        "source-understanding.json: URLを含めない",
        "source-understanding.json: アクセストークンらしき値を含めない",
    ]


def test_safety_allows_technical_filenames() -> None:
    value = {"terms": ["Node.js", "node.js", "package.json", "config.toml"]}

    assert _safety_errors(value, "source-understanding.json") == []


def test_understanding_accepts_separated_community_context(tmp_path) -> None:
    understanding = {
        "source_title": "技術解説",
        "central_question": "技術は実際にどう受け止められたか",
        "one_sentence_answer": "元資料の主張と公開反応を分けて理解する。",
        "major_claims": [
            {
                "id": "C1",
                "claim": "元資料の主張1",
                "evidence": "元資料内の根拠1",
                "limitation": "適用範囲1",
            },
            {
                "id": "C2",
                "claim": "元資料の主張2",
                "evidence": "元資料内の根拠2",
                "limitation": "適用範囲2",
            },
        ],
        "research": {
            "status": "completed",
            "search_queries": ["技術名 公式", "技術名 Hacker News"],
            "related_materials": [
                {
                    "id": "R1",
                    "material_type": "community_discussion",
                    "title": "公開議論",
                    "publisher": "Hacker News",
                    "finding": "導入コストへの懸念が複数投稿で述べられた。",
                    "relation_to_source": "元資料が触れた利用者反応の補助文脈。",
                    "evidential_role": "reception",
                    "confidence": "low",
                    "caveat": "投稿者の反応であり利用者全体を代表しない。",
                }
            ],
            "notes": [],
        },
        "prerequisite_terms": [],
        "concrete_examples": [],
        "source_limits": ["公開反応は代表性を保証しない。"],
        "teaching_risks": ["コミュニティ反応を技術的事実にしない。"],
    }
    (tmp_path / "source-understanding.json").write_text(
        json.dumps(understanding, ensure_ascii=False),
        encoding="utf-8",
    )

    result = validate_stage(tmp_path, "understanding")

    assert result["passed"] is True
    assert result["errors"] == []
