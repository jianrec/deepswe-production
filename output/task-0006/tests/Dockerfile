FROM deepswe-task-0006-environment:beca84e600e4
WORKDIR /tests
COPY . /tests/
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
