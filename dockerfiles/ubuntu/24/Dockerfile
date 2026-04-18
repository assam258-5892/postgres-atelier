FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG GIT_URL

# Locale & time zone
ENV LANG=en_US.UTF-8 LANGUAGE=en LC_ALL=en_US.UTF-8 TZ=Asia/Seoul
RUN apt update -y
RUN apt install -y locales tzdata curl wget bzip2 gnupg && \
    locale-gen en_US.UTF-8 && locale-gen ko_KR.UTF-8 && locale-gen ko_KR.EUC-KR && \
    update-locale && echo 'LANG="C.UTF-8"' > /etc/locale.conf && echo "Etc/UTC" > /etc/timezone
ENV LANG=ko_KR.UTF-8 LANGUAGE=ko:en LC_ALL=ko_KR.UTF-8

# OS packages
RUN apt install -y kmod init systemd cron openssh-server openssh-client rsync sudo findutils iproute2 iputils-ping net-tools htop btop ncdu plocate vim pandoc
RUN apt install -y gcc g++ gdb git make cmake valgrind lcov
RUN ( curl -fsSL http://www.jedsoft.org/releases/slang/slang-2.3.3.tar.bz2 | tar xj && cd slang-2.3.3 && ./configure && make && make install && cd .. && rm -rf slang-2.3.3 )
RUN ( curl -fsSL http://www.jedsoft.org/releases/jed/jed-0.99-19.tar.gz | tar xz && cd jed-0.99-19 && if [ "`/bin/arch`" = "aarch64" ]; then ./configure --build=aarch64-unknown-linux-gnu; else ./configure; fi && make && make install && cd .. && rm -rf jed-0.99-19 )
RUN update-alternatives --install /usr/bin/editor editor /usr/local/bin/jed 100
RUN apt install -y python3 python3-dev python3-pip python3-setuptools meson ninja-build && pip3 install powerline-status --break-system-packages

# PostgreSQL source build dependencies
RUN apt-get install -y pkg-config flex bison clang clang-format ccache fop gettext libxml2-utils xsltproc docbook-xml docbook-xsl libipc-run-perl libtext-trim-perl
RUN apt-get install -y libcairo2-dev libcurl4-openssl-dev libicu-dev libkrb5-dev libldap-dev liblz4-dev libnuma-dev libpam-dev libperl-dev libreadline-dev libselinux1-dev libssl-dev libsystemd-dev liburing-dev libxml2-dev libxslt1-dev libzstd-dev llvm-dev python3-dev systemtap-sdt-dev tcl-dev uuid-dev zlib1g-dev

# System tuning
RUN mkdir -p /etc/sysctl.d && echo 'fs.inotify.max_user_instances=4096' > /etc/sysctl.d/99-inotify.conf
RUN mkdir -p /etc/sysctl.d && echo 'kernel.core_pattern=/tmp/core-%e-%t-%p' > /etc/sysctl.d/99-core-pattern.conf
RUN mkdir -p /etc/ssh/sshd_config.d && echo "GatewayPorts yes" > /etc/ssh/sshd_config.d/02-gatewayport.conf && echo "AcceptEnv TERM_PROGRAM" > /etc/ssh/sshd_config.d/10-acceptenv.conf

# System-wide helpers
COPY --chown=root:root --chmod=755 files/gencov.py /usr/local/bin/gencov.py

# root shell environment
RUN printf "\nalias rm='rm -i'\nalias cp='cp -i'\nalias mv='mv -i'\nLANG=ko_KR.UTF-8\nLANGUAGE=ko:en\nLC_ALL=ko_KR.UTF-8\nTZ=Asia/Seoul\n\nalias remove='rm -f .*~ *~'\n\nulimit -n 1048576\nulimit -c unlimited\n" >> /root/.bashrc
RUN touch /root/.hushlogin

# postgres user (no PostgreSQL package is installed)
RUN groupadd -r postgres && useradd -m -r -g postgres -d /var/lib/postgresql -s /bin/bash postgres
RUN echo "postgres    ALL=(ALL)    NOPASSWD: ALL" > /etc/sudoers.d/postgres && chmod 440 /etc/sudoers.d/postgres

# Claude Code CLI
RUN su -l postgres -c "curl -fsSL https://claude.ai/install.sh | bash"

# postgres home defaults
COPY --chown=postgres:postgres --chmod=644 files/pgsql_profile /var/lib/postgresql/.bash_profile
RUN mkdir -p /var/lib/postgresql/.ssh && chmod 700 /var/lib/postgresql/.ssh && chown postgres:postgres /var/lib/postgresql/.ssh
COPY --chown=postgres:postgres --chmod=644 files/ssh_config /var/lib/postgresql/.ssh/config
RUN --mount=type=secret,id=git_credentials --mount=type=secret,id=ssh_privkey --mount=type=secret,id=ssh_pubkey --mount=type=secret,id=ssh_authkeys \
    install -o postgres -g postgres -m 600 /run/secrets/git_credentials /var/lib/postgresql/.git-credentials && \
    install -o postgres -g postgres -m 600 /run/secrets/ssh_privkey /var/lib/postgresql/.ssh/id_ed25519 && \
    install -o postgres -g postgres -m 644 /run/secrets/ssh_pubkey /var/lib/postgresql/.ssh/id_ed25519.pub && \
    install -o postgres -g postgres -m 644 /run/secrets/ssh_authkeys /var/lib/postgresql/.ssh/authorized_keys
RUN touch /var/lib/postgresql/.hushlogin && chown postgres:postgres /var/lib/postgresql/.hushlogin

# Environment (source build installs to $HOME/.local; data lives inside workspace volume)
ENV PATH=/var/lib/postgresql/.local/bin:${PATH}
ENV PGDATA=/var/lib/postgresql/workspace/pgdata PGUSER=postgres PGDATABASE=postgres

# Workspace skeleton
ENV GIT_CONFIG_GLOBAL=/var/lib/postgresql/workspace/.gitconfig
ADD  --chown=postgres:postgres files/postgres.tar.gz /var/lib/postgresql
COPY --chown=postgres:postgres --chmod=644 files/pgsql_bashrc /var/lib/postgresql/.bashrc
RUN mkdir -p /var/lib/postgresql/.vscode-server \
    && mkdir -p /var/lib/postgresql/.claude/projects/-var-lib-postgresql-workspace-postgres/memory \
    && mkdir -p /var/lib/postgresql/workspace \
    && mkdir -p /var/lib/postgresql/workspace/.vscode \
    && mkdir -p /var/lib/postgresql/workspace/postgres
COPY --chown=postgres:postgres --chmod=644 files/memory/MEMORY.md /var/lib/postgresql/.claude/projects/-var-lib-postgresql-workspace-postgres/memory/MEMORY.md
COPY --chown=postgres:postgres --chmod=644 files/psqlrc /var/lib/postgresql/.psqlrc
COPY --chown=postgres:postgres --chmod=644 files/gitconfig /var/lib/postgresql/workspace/.gitconfig
COPY --chown=postgres:postgres --chmod=644 files/gitignore /var/lib/postgresql/workspace/.gitignore
COPY --chown=postgres:postgres --chmod=644 files/*.json /var/lib/postgresql/workspace/.vscode/
RUN curl -o /var/lib/postgresql/.git-completion.bash https://raw.githubusercontent.com/git/git/master/contrib/completion/git-completion.bash
RUN ( cd /var/lib/postgresql/workspace/.vscode && git init && git add -A && git commit -m "Initial commit" )
RUN ( cd /var/lib/postgresql/workspace/postgres && git init && \
      if [ -n "$GIT_URL" ]; then git remote add origin "$GIT_URL"; fi && \
      git remote add postgres https://github.com/postgres/postgres.git && \
      git remote add upstream https://github.com/postgresql-cfbot/postgresql.git )
RUN chown -R postgres:postgres /var/lib/postgresql
RUN tar cvzf /var/lib/postgresql/.git.tar.gz -C /var/lib/postgresql/workspace .gitconfig .gitignore && chown postgres:postgres /var/lib/postgresql/.git.tar.gz
RUN tar cvzf /var/lib/postgresql/.vscode.tar.gz -C /var/lib/postgresql/workspace .vscode && chown postgres:postgres /var/lib/postgresql/.vscode.tar.gz
RUN tar cvzf /var/lib/postgresql/.postgres.tar.gz -C /var/lib/postgresql/workspace postgres && chown postgres:postgres /var/lib/postgresql/.postgres.tar.gz
RUN tar cvzf /var/lib/postgresql/.claude.tar.gz -C /var/lib/postgresql .claude/projects/-var-lib-postgresql-workspace-postgres/memory/MEMORY.md && chown postgres:postgres /var/lib/postgresql/.claude.tar.gz

# Persistent volumes and entrypoint
VOLUME /var/lib/postgresql/workspace /var/lib/postgresql/.vscode-server /var/lib/postgresql/.claude
ENTRYPOINT ["/usr/sbin/init"]
