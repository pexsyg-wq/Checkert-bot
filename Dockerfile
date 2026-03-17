
# Use an official Python runtime as a parent image
FROM python:3.11-slim-buster

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY requirements.txt .
COPY bot.py .
COPY google_credentials.json .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port the bot might use (Telegram bots don't typically expose ports, but good practice for webhooks)
# For polling, this is not strictly necessary.
# EXPOSE 80

# Run bot.py when the container launches
CMD ["python", "bot.py"]
