FROM rockylinux/rockylinux:9

ARG GIT_URL

# Locale & time zone
ENV LANG=en_US.UTF-8 LANGUAGE=en LC_ALL=en_US.UTF-8 TZ=Asia/Seoul
RUN dnf install -y --allowerasing glibc-langpack-en glibc-langpack-ko curl wget bzip2 && \
    echo 'LANG="C.UTF-8"' > /etc/locale.conf && echo "Etc/UTC" > /etc/timezone
ENV LANG=ko_KR.UTF-8 LANGUAGE=ko:en LC_ALL=ko_KR.UTF-8

# EPEL
RUN dnf install -y http://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm && crb enable

# OS packages
RUN dnf install -y cronie openssh-server openssh-clients rsync sudo ncurses procps-ng findutils iproute iputils net-tools htop btop ncdu mlocate vim-enhanced pandoc
RUN dnf install -y gcc gcc-c++ gdb git make cmake valgrind lcov redhat-rpm-config valgrind-devel
RUN ( curl -fsSL http://www.jedsoft.org/releases/slang/slang-2.3.3.tar.bz2 | tar xj && cd slang-2.3.3 && ./configure && make && make install && cd .. && rm -rf slang-2.3.3 )
RUN ( curl -fsSL http://www.jedsoft.org/releases/jed/jed-0.99-19.tar.gz | tar xz && cd jed-0.99-19 && if [ "`/bin/arch`" = "aarch64" ]; then ./configure --build=aarch64-unknown-linux-gnu; else ./configure; fi && make && make install && cd .. && rm -rf jed-0.99-19 )
RUN update-alternatives --install /usr/bin/editor editor /usr/local/bin/jed 100
RUN dnf install -y python3 python3-devel python3-pip python3-setuptools meson ninja-build && pip3 install powerline-status

# PostgreSQL source build dependencies
RUN dnf install -y flex bison clang clang-tools-extra docbook-dtds docbook-style-xsl perl-core perl-IPC-Run
RUN dnf install -y gettext-devel krb5-devel libcurl-devel libicu-devel libselinux-devel liburing-devel libuuid-devel libxml2-devel libxslt-devel libzstd-devel llvm llvm-devel ccache lz4-devel numactl-devel openldap-devel openssl-devel pam-devel perl-ExtUtils-Embed readline-devel systemd-devel systemtap-sdt-devel tcl-devel zlib-devel

# System tuning
RUN mkdir -p /etc/sysctl.d && echo 'fs.inotify.max_user_instances=4096' > /etc/sysctl.d/99-inotify.conf
RUN mkdir -p /etc/sysctl.d && echo 'kernel.core_pattern=/tmp/core-%e-%t-%p' > /etc/sysctl.d/99-core-pattern.conf
RUN mkdir -p /etc/ssh/sshd_config.d && echo "GatewayPorts yes" > /etc/ssh/sshd_config.d/02-gatewayport.conf && echo "AcceptEnv TERM_PROGRAM" > /etc/ssh/sshd_config.d/10-acceptenv.conf

# System-wide helpers
COPY --chown=root:root --chmod=755 files/gencov.py /usr/local/bin/gencov.py

# root shell environment
RUN printf "\nLANG=ko_KR.UTF-8\nLANGUAGE=ko:en\nLC_ALL=ko_KR.UTF-8\nTZ=Asia/Seoul\n\nalias remove='rm -f .*~ *~'\n\nulimit -n 1048576\nulimit -c unlimited\n" >> /root/.bashrc
RUN touch /root/.hushlogin

# postgres user (no PostgreSQL package is installed)
RUN groupadd -r postgres && useradd -m -r -g postgres -d /var/lib/pgsql -s /bin/bash postgres
RUN echo "postgres    ALL=(ALL)    NOPASSWD: ALL" > /etc/sudoers.d/postgres && chmod 440 /etc/sudoers.d/postgres

# Claude Code CLI
RUN su -l postgres -c "curl -fsSL https://claude.ai/install.sh | bash"

# postgres home defaults
COPY --chown=postgres:postgres --chmod=644 files/pgsql_profile /var/lib/pgsql/.bash_profile
RUN mkdir -p /var/lib/pgsql/.ssh && chmod 700 /var/lib/pgsql/.ssh && chown postgres:postgres /var/lib/pgsql/.ssh
COPY --chown=postgres:postgres --chmod=644 files/ssh_config /var/lib/pgsql/.ssh/config
RUN --mount=type=secret,id=git_credentials --mount=type=secret,id=ssh_privkey --mount=type=secret,id=ssh_pubkey --mount=type=secret,id=ssh_authkeys \
    install -o postgres -g postgres -m 600 /run/secrets/git_credentials /var/lib/pgsql/.git-credentials && \
    install -o postgres -g postgres -m 600 /run/secrets/ssh_privkey /var/lib/pgsql/.ssh/id_ed25519 && \
    install -o postgres -g postgres -m 644 /run/secrets/ssh_pubkey /var/lib/pgsql/.ssh/id_ed25519.pub && \
    install -o postgres -g postgres -m 644 /run/secrets/ssh_authkeys /var/lib/pgsql/.ssh/authorized_keys
RUN touch /var/lib/pgsql/.hushlogin && chown postgres:postgres /var/lib/pgsql/.hushlogin

# Environment (source build installs to $HOME/.local; data lives inside workspace volume)
ENV PATH=/var/lib/pgsql/.local/bin:${PATH}
ENV PGDATA=/var/lib/pgsql/workspace/pgdata PGUSER=postgres PGDATABASE=postgres

# Workspace skeleton
ENV GIT_CONFIG_GLOBAL=/var/lib/pgsql/workspace/.gitconfig
ADD  --chown=postgres:postgres files/postgres.tar.gz /var/lib/pgsql
COPY --chown=postgres:postgres --chmod=644 files/pgsql_bashrc /var/lib/pgsql/.bashrc
RUN mkdir -p /var/lib/pgsql/.vscode-server \
    && mkdir -p /var/lib/pgsql/.claude/projects/-var-lib-pgsql-workspace/memory \
    && mkdir -p /var/lib/pgsql/workspace \
    && mkdir -p /var/lib/pgsql/workspace/.vscode \
    && mkdir -p /var/lib/pgsql/workspace/postgres
COPY --chown=postgres:postgres --chmod=644 files/MEMORY.md /var/lib/pgsql/.claude/projects/-var-lib-pgsql-workspace/memory/MEMORY.md
COPY --chown=postgres:postgres --chmod=644 files/psqlrc /var/lib/pgsql/.psqlrc
COPY --chown=postgres:postgres --chmod=644 files/gitconfig /var/lib/pgsql/workspace/.gitconfig
COPY --chown=postgres:postgres --chmod=644 files/gitignore /var/lib/pgsql/workspace/.gitignore
COPY --chown=postgres:postgres --chmod=644 files/*.json /var/lib/pgsql/workspace/.vscode/
RUN curl -o /var/lib/pgsql/.git-completion.bash https://raw.githubusercontent.com/git/git/master/contrib/completion/git-completion.bash
RUN ( cd /var/lib/pgsql/workspace/.vscode && git init && git add -A && git commit -m "Initial commit" )
RUN ( cd /var/lib/pgsql/workspace/postgres && git init && \
      if [ -n "$GIT_URL" ]; then git remote add origin "$GIT_URL"; fi && \
      git remote add postgres https://github.com/postgres/postgres.git && \
      git remote add upstream https://github.com/postgresql-cfbot/postgresql.git )
RUN chown -R postgres:postgres /var/lib/pgsql
RUN tar cvzf /var/lib/pgsql/.git.tar.gz -C /var/lib/pgsql/workspace .gitconfig .gitignore && chown postgres:postgres /var/lib/pgsql/.git.tar.gz
RUN tar cvzf /var/lib/pgsql/.vscode.tar.gz -C /var/lib/pgsql/workspace .vscode && chown postgres:postgres /var/lib/pgsql/.vscode.tar.gz
RUN tar cvzf /var/lib/pgsql/.postgres.tar.gz -C /var/lib/pgsql/workspace postgres && chown postgres:postgres /var/lib/pgsql/.postgres.tar.gz
RUN tar cvzf /var/lib/pgsql/.claude.tar.gz -C /var/lib/pgsql .claude/projects/-var-lib-pgsql-workspace/memory/MEMORY.md && chown postgres:postgres /var/lib/pgsql/.claude.tar.gz

# Persistent volumes and entrypoint
VOLUME /var/lib/pgsql/workspace /var/lib/pgsql/.vscode-server /var/lib/pgsql/.claude
ENTRYPOINT ["/usr/sbin/init"]
