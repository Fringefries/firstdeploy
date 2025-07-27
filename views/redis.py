import redis, pickle

r = redis.StrictRedis()

class RS:
    def delete(key):
        r.delete(key)

    def get(key):
        data = r.get(key)
        if data:
            return pickle.loads(data)
        return None

    def set(key, data, ex=0):
        try:
            data = pickle.dumps(data)
            if ex:
                ex = int(ex)
                r.set(key, data, ex=ex)
            else:
                r.set(key, data)
        except Exception as e:
            print(e)
            
    
    def get_relay_state():
        data = RS.get('relay_states')
        
        if not data:
            data = {
                1:1
            }
            RS.set('relay_states', data)
            
        return data
    
    def update_state(pin, state):
        data = RS.get('relay_states')
        data[pin] = state
        RS.set('relay_states', data)