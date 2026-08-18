FROM deepswe-task-0002-environment:144893850fa7
WORKDIR /tests
COPY . /tests/
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
