import bcrypt

password = "admin123"  # Cambia con la tua password, se vuoi
hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
print(hashed)