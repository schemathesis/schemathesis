## API under test

The tutorial tests a booking API that creates hotel reservations and looks them up. It lives in the [Schemathesis repository](https://github.com/schemathesis/schemathesis/tree/master/examples/booking){target=_blank}:

```console
git clone https://github.com/schemathesis/schemathesis.git
cd schemathesis/examples/booking
docker compose up -d --build
```

!!! success "Verify the API is running"

    Open [http://localhost:8080/docs](http://localhost:8080/docs){target=_blank} - you should see the interactive API documentation.

Every booking endpoint requires the header `Authorization: Bearer secret-token`.
