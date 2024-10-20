FROM rust:1.82.0 AS build

RUN git clone \
    --single-branch \
    --branch v0.2.11 \
    https://github.com/timvisee/lazymc.git /lazymc-src
WORKDIR /lazymc-src

RUN cargo build --release

FROM python:3

VOLUME ["/config"]

WORKDIR /app
COPY --from=build /lazymc-src/target/release/lazymc /app/lazymc

COPY requirements.txt /app
RUN pip3 install -r requirements.txt

COPY main.py .

ENTRYPOINT /app/lazymc -c /config/lazymc.toml start
