#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCANNER="${SCRIPT_DIR}/.venv/bin/a-share-macd-scan"
LOG_FILE="logs/scanner.log"
ARGS=("$@")

cd "${SCRIPT_DIR}"
for ((index = 0; index < ${#ARGS[@]}; index++)); do
    case "${ARGS[index]}" in
        --log-file)
            ((index += 1))
            LOG_FILE="${ARGS[index]}"
            ;;
        --log-file=*)
            LOG_FILE="${ARGS[index]#--log-file=}"
            ;;
    esac
done
mkdir -p "$(dirname -- "${LOG_FILE}")"

if [[ ! -x "${SCANNER}" ]]; then
    echo "$(date '+%F %T') ERROR 未找到可执行扫描器：${SCANNER}" >> "${LOG_FILE}"
    echo "$(date '+%F %T') ERROR 请先在 ${SCRIPT_DIR} 安装项目依赖。" >> "${LOG_FILE}"
    exit 1
fi

exec "${SCANNER}" "$@" >> "${LOG_FILE}" 2>&1
