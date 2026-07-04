# Taxonomy Router V1

`taxonomy_layer` is a pre-storage classification layer for `ComposedCardV1`.
It does not call OCR, comments, model providers, SiYuan, or Lucas Database.

Flow:

```text
ComposedCardV1-like dict
-> TaxonomyRouter.route_card(card)
-> existing taxonomy seed
-> simple keyword / token-overlap / fuzzy matching
-> TaxonomyDecisionV1
```

V1 rules:

- Prefer existing categories from the seed taxonomy.
- Route low-confidence cards to `Inbox / 待分类`.
- Do not automatically create categories.
- Only emit `CategoryCreateProposalV1` when no similar category exists and the card has enough topic signal.
- Business-domain automation such as ecommerce customer service stays in Inbox with a user-reviewed proposal until a stable industry taxonomy exists.
- Treat `AI` as a domain only when AI systems are the topic. If AI is just the tool used inside another field, route by the field first.
- Front-end animation topics such as GSAP / ScrollTrigger route to `软件工程 / 前端开发 / 动画与交互`; AI-assisted page or component generation routes to `软件工程 / 前端开发 / AI 辅助前端开发`.
- Keep storage paths outside this layer; sinks should consume the decision later.

Validation:

```powershell
python -m compileall taxonomy_layer tests
python -m unittest tests/test_taxonomy_router.py -v
```
