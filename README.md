---
title: VeriClaim
emoji: 🔍
colorFrom: blue
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# VeriClaim — Multi-Modal AI Insurance Claims Agent

## Required Secrets (HF Spaces → Settings → Repository Secrets)

| Secret              | Description                                      |
|---------------------|--------------------------------------------------|
| GEMINI_API_KEY      | Google Gemini API key                            |
| SUPABASE_URL        | Your Supabase project URL                        |
| SUPABASE_ANON_KEY   | Supabase public anon key (safe for frontend)     |
| SUPABASE_SERVICE_KEY| Supabase service role key (backend only)         |
| SUPABASE_DB_URL     | postgres:// direct connection string             |
| ADMIN_USERNAME      | Admin portal username                            |
| ADMIN_PASSWORD      | Admin portal password                            |
| ALLOWED_ORIGINS     | Your HF Spaces URL e.g. https://yourname-vericlaim.hf.space |

## Pre-Deploy Checklist
- [ ] Run supabase_schema_v4.sql in Supabase SQL Editor
- [ ] All 8 secrets added in HF Spaces settings
- [ ] Supabase Storage bucket named "evidence" exists
- [ ] Supabase Storage bucket "evidence" has RLS enabled
