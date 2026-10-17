# The team runs in here; the bench's keys, grader and references never do.
#
# Built from a context holding only agyteam/ and the packaging files (see
# bench/convergence.py --docker), so nothing under bench/ or evals/ is in the
# image at all. The staged fixtures, the team's own directory and the audit
# log are mounted at the same absolute paths they have on the host, so every
# path in the record means the same thing on both sides.
FROM python:3.12-slim
RUN pip install --no-cache-dir "google-antigravity==0.1.18" "google-genai>=1.0.0" pytest tzdata
WORKDIR /app
COPY agyteam /app/agyteam
COPY pyproject.toml README.md /app/
RUN pip install --no-cache-dir --no-deps /app && rm -rf /app/build /app/*.egg-info
ENV PYTHONUNBUFFERED=1
