FROM deepswe-task-0004-environment:ec61f187fe39
WORKDIR /tests
COPY . /tests/
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
