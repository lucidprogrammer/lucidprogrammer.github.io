# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
# Assuming requirements.txt will be created or provided later
# For now, we'll copy the application files directly
# COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY app.py .
COPY admin-dashboard /app/admin-dashboard
# If there are other portal types with their own static/template folders, copy them too
# COPY internal-portal /app/internal-portal
# COPY external-portal /app/external-portal

# Make port 80 available to the world outside this container
# The actual port is passed via command line to app.py,
# so EXPOSE is more for documentation or for specific Docker networking setups.
# EXPOSE 5000

# Define environment variables if necessary (e.g., for Keycloak URL, Realm)
# ENV KEYCLOAK_SERVER_URL=http://host.docker.internal:8080
# ENV KEYCLOAK_REALM=enterprise-sso

# Run app.py when the container launches
# The command line arguments will specify which portal and port to use.
# Example: python app.py --portal=admin --port=3003
CMD ["python", "app.py"]
