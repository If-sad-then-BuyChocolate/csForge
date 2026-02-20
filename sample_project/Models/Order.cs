using System;

namespace SampleProject.Models
{
    public class Order
    {
        public Guid OrderId { get; set; }
        public Guid CustomerGUID { get; set; }
        public float Test { get; set; }
    }
}