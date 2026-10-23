See AGENTS.md for the development principles.

# Models for testing

- Use only DeepSeek Flash (`deepseek/deepseek-flash`) for every model call you start:
  writing, checking, judging, visual judging, evals and dev scripts.
- Never use DeepSeek V4 Pro (`deepseek/deepseek-v4-pro`).
- The quality eval: pass `--model deepseek/deepseek-flash --judge deepseek/deepseek-flash`
  (Flash is also the default judge).
