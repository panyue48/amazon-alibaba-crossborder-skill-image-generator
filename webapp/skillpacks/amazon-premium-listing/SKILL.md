---
name: amazon-premium-listing
description: Generate Amazon-focused premium listing visuals and dynamic prompt packs for hero images, infographic detail images, and lifestyle scenes. Use when the project needs Amazon-targeted product visuals, live best-seller inspiration, or prompt updates driven by Amazon Best Sellers pages.
---

# Amazon Premium Listing

Load `skill.json` as the runtime manifest.

## Workflow

1. Read the selected category and map it to the configured Amazon Best Sellers URL.
2. Fetch the live public ranking page and extract sample titles, links, images, pricing, and review signals.
3. Distill high-frequency commercial keywords from the live titles.
4. Blend those keywords into the Amazon prompt template with the user product name, selling points, and uploaded reference images.
5. Keep the prompt conversion-oriented, premium, and suitable for direct Amazon listing use.

## Output Bias

- Favor clean composition, crisp product edges, and strong material realism.
- Prefer short English callouts for infographic images.
- Avoid noisy backgrounds, clutter, and low-end poster styles.
