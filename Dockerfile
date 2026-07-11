FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

# Claude Code refuse de tourner en root sans sandbox : utilisateur dédié
RUN useradd -m -u 1001 agent
USER agent
ENV HOME=/home/agent

COPY --chown=agent agent/ /agent/
CMD ["sh", "/agent/run.sh"]
