use axum::{routing::get, Router};
use std::net::SocketAddr;

fn main() {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("Failed to build Tokio runtime")
        .block_on(async {
            let app = Router::new().route("/success/", get(crash));
            let addr = SocketAddr::from(([127, 0, 0, 1], 3000));
            axum::Server::bind(&addr)
                .serve(app.into_make_service())
                .await
                .expect("Failed to start server");
        })
}

async fn crash() -> &'static str {
    panic!("Error")
}
