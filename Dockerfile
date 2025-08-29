# Start from Ubuntu 22.04
FROM ubuntu:22.04

# Install basic build tools
RUN apt-get update && apt-get install -y \
    wget curl unzip git software-properties-common build-essential \
    libssl-dev libbz2-dev libreadline-dev libsqlite3-dev \
    zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libffi-dev \
    && apt-get clean

# ------------------ Install Python 3.13.3 ------------------
RUN wget https://www.python.org/ftp/python/3.13.3/Python-3.13.3.tgz -O /tmp/python3.13.3.tgz && \
    tar xzf /tmp/python3.13.3.tgz -C /tmp && \
    cd /tmp/Python-3.13.3 && \
    ./configure --enable-optimizations && \
    make -j$(nproc) && make altinstall && \
    ln -sf /usr/local/bin/python3.13 /usr/bin/python3 && \
    python3 -m pip install --upgrade pip && \
    rm -rf /tmp/Python-3.13.3 /tmp/python3.13.3.tgz

# ------------------ Install JDKs ------------------
# JDK 8
RUN apt-get install -y openjdk-8-jdk
# JDK 11
RUN apt-get install -y openjdk-11-jdk
# JDK 17
RUN apt-get install -y openjdk-17-jdk
# JDK 21 (manual download)
RUN wget https://download.oracle.com/java/21/latest/jdk-21_linux-x64_bin.tar.gz -O /tmp/jdk-21.tar.gz && \
    mkdir -p /usr/lib/jvm/jdk-21 && \
    tar -xzf /tmp/jdk-21.tar.gz -C /usr/lib/jvm/jdk-21 --strip-components=1 && \
    rm /tmp/jdk-21.tar.gz

# Set JDK environment variables
ENV JAVA_HOME8=/usr/lib/jvm/java-8-openjdk
ENV JAVA_HOME11=/usr/lib/jvm/java-11-openjdk
ENV JAVA_HOME17=/usr/lib/jvm/java-17-openjdk
ENV JAVA_HOME21=/usr/lib/jvm/jdk-21

# ------------------ Install Maven, Gradle, Ant ------------------
RUN apt-get install -y maven ant gradle

# ------------------ Install Python libraries ------------------
RUN pip3 install pandas requests python-dotenv

# ------------------ Copy script ------------------
WORKDIR /app
COPY compiler.py requirements.txt ./

# ------------------ Default command ------------------
CMD ["python3","compiler.py"]
