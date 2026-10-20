# Take-Home Task - Payment Endpoint

Source: [Solid Software take-home task](https://solidsoftwarehq.notion.site/Take-Home-Task-Payment-Endpoint-3b377c5a0848802ba270c61dcc345c5e)


## The context

We build an online shop. The shop sells physical products.

A user adds products to a cart. The user then pays for the cart with a credit card.

We already have these parts of the system:

- The user account, with an id, email and a name.

- The cart, with the products that the user selected.

- Payment total calculation service. You don’t need to calculate how much to pay.

- The payment details of the user. Each user has a card token from our payment provider.

You must build the payment part.

## What to make

1. **A SQL schema** for the payment part of the system.

1. **An HTTP endpoint** that starts the payment for a cart.

## Rules

- Use Python, Flask and SQLAlchemy.

- Use PostgreSQL for the database.

- Use the following base schema: [Quick-start SQL schema](quick-start-schema.sql)

- You do not have to connect to a real payment provider. A mock function is enough. The mock can succeed or fail - you decide how.

- Write tests for the endpoint.

- Put the code in a Git repository. Add a README that tells us how to run the code and the tests.

If a requirement is not clear, use your best judgment. Write your assumptions in the README.

## How to send us your work

Send us a link to your Git repository.

## FAQ

### *Why do we ask you to do a test task?*

This is a great way for you to demonstrate all your knowledge and skills in a usual atmosphere without hurry and interview stress. And, at the same time, it is a great way for us to notice your talent.

### ***What result are we waiting for?***

- Ready application that is working correctly (without bugs);

- The code is clean, good naming (see Clean Code book by Robert C. Martin);

- DRY, KISS principles applied;

- You fully understand all code you written, how it works, why any parameter used, etc;

- Finished task meets all requirements.

### ***How much time will you have to complete the Test Task?***

There are no timeframes for performing the test task, but of course, the sooner the better :) Quality has higher priority for us than the speed, so please take your time and send us the link as soon as the task will be complete.

### ***What should you expect after the completion of a test task?***

- Your test task will be reviewed by our Technical Specialist;

- We will return with the results as soon as possible;

- Please don’t delete your test task until the recruitment process is complete. We may get back to the completed test task in further stages.

- To avoid sharing solutions between candidates, we don’t provide detailed feedback on your test task submission after our review (we don’t point out any issues found).

***Please contact us in case of any queries and let us know if you have any additional questions.***

***Good luck with the test task!***
