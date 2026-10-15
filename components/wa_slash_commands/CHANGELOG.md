# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-04-18

### Added
- **Diagnostic Helper (`test_api.py`)**: Added a standalone script to verify Gemini API key validity, testing both standard text generation and strict JSON schema generation.
- **Enhanced Console Visibility**: Added explicit terminal logging when commands are invoked. The terminal will now print `[Command] Context Loaded` with the total messages pulled, the earliest timestamp, and the API payload size to help debug empty responses.

### Changed
- **Dynamic Context Truncation**: Modified the `engine.py` formatting engine to actively trim chat histories down to a maximum character payload (default ~100,000 chars) before passing to the Gemini API. This prioritizes the *most recent* context and prevents `400 Bad Request` or Payload Too Large errors on highly active groups when running commands like `/sotu` or `/recap` which query up to 2,000 messages.

## [1.2.0] - 2026-04-18
### Added
- **AI Model Evaluation Harness (`evaluate_models.py`)**: A powerful testing tool that benchmarks multiple Gemini and Gemma models against your real WhatsApp history. It automatically finds your most active group, runs simulated commands, and uses a "Judge" AI to grade the quality, hallucinations, and context retention of each model.
