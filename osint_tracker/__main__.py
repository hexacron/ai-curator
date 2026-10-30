"""CLI: `python -m osint_tracker` runs one collection."""
from __future__ import annotations

import argparse
import logging
from typing import List, Optional

from dotenv import load_dotenv

from github_client import GitHubClient, token_from_env
from osint_tracker.collect import collect
from osint_tracker.config import load_config
from llm_client import AnthropicClient

logger = logging.getLogger(__name__)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m osint_tracker',
                                     description="Record today's snapshot of emerging OSINT repositories.")
    parser.add_argument('--config', default='config.json', help="config file; its 'osint' section overrides defaults")
    parser.add_argument('--data-dir', default='data/osint', help='directory holding state.jsonl')
    parser.add_argument('--dry-run', action='store_true', help='do everything except write the state file')
    parser.add_argument('--no-llm', action='store_true', help='skip LLM review even if ANTHROPIC_API_KEY is set')
    parser.add_argument('--review-limit', type=int, metavar='N', help='override llm.max_calls_per_run for this run')
    args = parser.parse_args(argv)

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    try:
        config = load_config(args.config)
        if args.review_limit is not None:
            config['llm']['max_calls_per_run'] = max(0, args.review_limit)
        client = GitHubClient(token_from_env())
        client.validate_token()
        llm = None
        if config['llm']['enabled'] and not args.no_llm:
            llm = AnthropicClient.from_env(config['llm']['timeout_seconds'])
            logger.info("LLM review enabled" if llm else "ANTHROPIC_API_KEY not set: running rules-only")
        else:
            config['llm']['enabled'] = False
        summary = collect(client, config, args.data_dir, dry_run=args.dry_run, llm=llm)
    except Exception as e:
        logger.error(f"OSINT tracker failed: {e}", exc_info=True)
        raise

    suffix = ' (dry run, nothing written)' if args.dry_run else f' -> {args.data_dir}'
    logger.info("Collection finished" + suffix + ": " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
