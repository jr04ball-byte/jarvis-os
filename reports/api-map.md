# Jarvis OS — API Map (generated Cycle 7, AST-measured: 97 routes)

Conventions: all handlers `async` unless noted; `/v1/*` is JSON, `/` hosts pages/assets.

## System / health (GET)
| Method | Path | Handler |
|---|---|---|
| GET | `/` | `root` |
| GET | `/health` | `health_check` |
| GET | `/ready` | `readiness_check` |
| GET | `/stats` | `get_stats` |
| GET | `/v1/system/status` | `system_status` |
| GET | `/v1/system/compute` | `system_compute` |
| GET | `/v1/performance` | `get_performance` |
| GET | `/v1/command-center/overview` | `command_center_overview` |
| GET | `/v1/agent/pending` | `agent_pending` |

## Chat / brains (POST unless noted)
| Method | Path | Handler |
|---|---|---|
| POST | `/v1/chat/completions` | `chat_completion` |
| POST | `/v1/chat/completions-rag` | `chat_with_rag` |
| POST | `/v1/compare` | `compare_models` |
| POST | `/v1/sales/chat` | `sales_chat` |
| POST | `/v1/agent/chat` | `agent_chat` |
| POST | `/v1/agent/confirm` | `agent_confirm` |
| POST | `/v1/gemini/chat` | `gemini_chat` |
| POST | `/v1/gemini/live-token` | `gemini_live_token` |
| GET | `/v1/gemini/status` | `gemini_status` |
| GET | `/v1/models` | `list_models` |

## Telemetry (V24 P5)
| Method | Path | Handler |
|---|---|---|
| GET | `/v1/telemetry/summary` | `telemetry_summary` |

## Conversations / documents
| Method | Path | Handler |
|---|---|---|
| POST | `/v1/conversations` | `create_conversation` |
| GET | `/v1/conversations` | `list_conversations` |
| GET | `/v1/conversations/{conv_id}` | `get_conversation` |
| POST | `/v1/conversations/{conv_id}/messages` | `add_conversation_message` |
| POST | `/v1/documents` | `upload_document` |
| POST | `/v1/documents/upload-file` | `upload_file` |
| POST | `/v1/research/search` | `research_search` |

## Artifacts
| Method | Path | Handler |
|---|---|---|
| POST | `/v1/artifacts` | `artifact_create` |
| GET | `/v1/artifacts` | `artifacts_list` |
| GET | `/v1/artifacts/{artifact_id}` | `artifact_get` |
| PATCH | `/v1/artifacts/{artifact_id}` | `artifact_patch` |
| DELETE | `/v1/artifacts/{artifact_id}` | `artifact_delete` |

## Voice
| Method | Path | Handler |
|---|---|---|
| POST | `/v1/voice/turn` | `voice_turn` |
| GET | `/voice`, `/voice-live`, `/voice-live.html`, `/voice-engine.js` | `voice_ui`, `voice_live_ui`, `voice_engine_js` |
| GET | `/v1/deepgram-status` | `deepgram_status` |
| POST | `/v1/deepgram-token` | `deepgram_token` |
| POST | `/v1/deepgram-speak`, `/v1/deepgram-speak-stream` | `deepgram_speak`, `deepgram_speak_stream` |

## Orchestrator (project plans)
| Method | Path | Handler |
|---|---|---|
| GET | `/v1/orchestrator/policy` | `orchestrator_policy` |
| GET | `/v1/orchestrator/targets` | `orchestrator_targets` |
| GET | `/v1/orchestrator/projects/{project_id}` | `orchestrator_project` |
| GET | `/v1/orchestrator/projects/{project_id}/next` | `orchestrator_next` |
| GET | `/v1/orchestrator/projects/{project_id}/audit` | `orchestrator_audit` |

## Project worker
| Method | Path | Handler |
|---|---|---|
| POST | `/v1/project-worker/inspect` | `project_worker_inspect` |
| POST | `/v1/project-worker/health` | `project_worker_health` |
| POST | `/v1/project-worker/verify` | `project_worker_verify` |
| POST | `/v1/project-worker/implement` | `project_worker_implement` |
| GET | `/v1/project-worker/runs/{run_id}` | `project_worker_run` |

## Tools / computer / model-lab / connections
| Method | Path | Handler |
|---|---|---|
| GET | `/v1/tools`, `/v1/tools/local` | `list_tools`, `local_tools_inventory` |
| POST | `/v1/tools/execute` | `execute_tool` |
| GET | `/v1/computer/capabilities|observe|processes|screenshot|windows` | `computer_*_route` |
| GET | `/v1/model-lab/overview|huggingface|runtimes` | `model_lab_*` |
| POST | `/v1/model-lab/benchmark|lmstudio/load` | `model_lab_benchmark_route`, `model_lab_load` |
| GET | `/v1/connections` | `list_connections` |
| POST | `/v1/connections/{id}/enable|/test` | `set_connection`, `test_connection` |

## Google integrations
| Method | Path | Handler |
|---|---|---|
| GET | `/auth/google/start|callback|status` | `auth_google_*` |
| POST | `/auth/google/disconnect` | `auth_google_disconnect` |
| GET | `/gmail/messages`, `/calendar/events` | `gmail_messages`, `calendar_events` |

## UI pages / assets (GET)
`/dashboard[.html]`, `/dashboard-classic[.html]`, `/godseye[.html]`,
`/companion`, `/companion-manifest.json`, `/companion-sw.js`,
`/{orb,bg,command-center}-loop.{mp4,webm}`, `/*-poster.png` → `blender_dashboard_asset`.
