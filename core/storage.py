class FieldStorage:
    def __init__(self, required_fields): #init function to initialize the storage with required fields
        self.fields = required_fields
        self.data = {field: None for field in required_fields}
        self.logs=[]

    def get_data(self): #self is used to access the instance of the class and get the data stored in the storage
        return self.data
    
    def update_field(self, field, value):
        if field in self.fields:
            self.data[field] = value
            self.logs.append(f"Updated field '{field}' with value '{value}'")
        else:
            # raise ValueError(f"Field '{field}' is not a valid field. Valid fields are: {self.fields}")
            self.logs.append(f"Failed to update field '{field}' with value '{value}' - Invalid Field")
            return False, "Updating Failed: Invalid Field"  
        return True, "Updated"

    def set_data(self, field, value):
        if field in self.fields:
            self.data[field] = value
    
    def get_missing_fields(self):
        return [field for field in self.fields if self.data[field] is None]
    
    def is_complete(self):
        return all(self.data[field] is not None for field in self.fields)
    def get_logs(self):
        return self.logs