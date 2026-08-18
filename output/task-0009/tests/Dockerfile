FROM deepswe-task-0009-environment:85293e3e2b88
WORKDIR /tests
COPY . /tests/
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
